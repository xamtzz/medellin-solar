import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import st_folium
import rasterio
from rasterio.mask import mask
import os
import base64
from io import BytesIO
import numpy as np
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject
from shapely.geometry import box

st.set_page_config(page_title="Potencial Fotovoltaico Medellín", page_icon="☀️", layout="wide")

st.title(" Visualizador de Potencial Fotovoltaico en Medellín")
st.markdown("Calcula y visualiza el potencial fotovoltaico en techos, considerando el factor de sombra.")

@st.cache_data
def load_barrios():
    # Load neighborhoods
    barrios_path = os.path.join("datos", "barrios_y_veredas_limpio.geojson")
    if os.path.exists(barrios_path):
        return gpd.read_file(barrios_path)
    return None

@st.cache_data
def load_polygons(barrio_name, _barrio_geom, _source_crs):
    polygons_path = os.path.join("datos", "poligonosmedellin_fotovoltaico.gpkg")
    if os.path.exists(polygons_path):
        # 1. Read 1 row to get the GPKG CRS
        try:
            sample = gpd.read_file(polygons_path, rows=1)
            target_crs = sample.crs
        except:
            target_crs = None
            
        # 2. Project barrio_geom to target_crs if needed
        geom_to_intersect = _barrio_geom
        if target_crs and _source_crs and target_crs != _source_crs:
            temp_gdf = gpd.GeoDataFrame(geometry=[_barrio_geom], crs=_source_crs)
            temp_gdf = temp_gdf.to_crs(target_crs)
            geom_to_intersect = temp_gdf.geometry.iloc[0]
            
        bbox = tuple(geom_to_intersect.bounds)
        polys = gpd.read_file(polygons_path, bbox=bbox)
        
        if not polys.empty:
            polys = polys[polys.geometry.intersects(geom_to_intersect)]
            
            # Project back to WGS84 for mapping if needed
            if polys.crs and polys.crs.to_epsg() != 4326:
                polys = polys.to_crs(epsg=4326)
                
        return polys
    return None

def calculate_shadows(polys, tif_path):
    if not os.path.exists(tif_path):
        polys['shadow_factor'] = 0.8
        return polys
        
    shadow_factors = []
    
    with rasterio.open(tif_path) as src:
        tif_crs = src.crs
        
        # Project polygons to TIF CRS if needed
        if polys.crs and polys.crs != tif_crs:
            polys_proj = polys.to_crs(tif_crs)
        else:
            polys_proj = polys.copy()
            
        for geom in polys_proj.geometry:
            try:
                out_image, out_transform = mask(src, [geom], crop=True)
                valid_data = out_image[0]
                if src.nodata is not None:
                    valid_data = valid_data[valid_data != src.nodata]
                
                if valid_data.size > 0:
                    # En estos TIFF: 1 = sombra y 0 = iluminado.
                    shadow_fraction = np.mean(valid_data)
                    shadow_factors.append(1 - shadow_fraction)
                else:
                    shadow_factors.append(0.8)
            except Exception:
                shadow_factors.append(0.8)
                
    polys['shadow_factor'] = shadow_factors
    return polys

@st.cache_data
def calculate_zone_shadow_stats(tif_path, _zone_geom, _zone_crs):
    """Calcula sol y sombra por píxel dentro del límite exacto de la zona."""
    with rasterio.open(tif_path) as src:
        geom = _zone_geom
        if _zone_crs and src.crs and _zone_crs != src.crs:
            geom = gpd.GeoSeries([geom], crs=_zone_crs).to_crs(src.crs).iloc[0]

        zone_data = mask(src, [geom], crop=True)[0][0]
        valid = zone_data != src.nodata if src.nodata is not None else np.ones(zone_data.shape, dtype=bool)
        shadow_pixels = int(np.count_nonzero((zone_data == 1) & valid))
        sun_pixels = int(np.count_nonzero((zone_data == 0) & valid))
        total_pixels = sun_pixels + shadow_pixels

        if total_pixels == 0:
            return None
        return {
            "sun_pct": sun_pixels / total_pixels * 100,
            "shadow_pct": shadow_pixels / total_pixels * 100,
            "total_pixels": total_pixels,
        }

@st.cache_data
def load_city_shadow_overlay(tif_path, max_size=1400):
    """Convierte el raster de sombras a una capa PNG ligera para Folium."""
    with rasterio.open(tif_path) as src:
        src_data = src.read(1)
        valid = src_data != src.nodata if src.nodata is not None else np.ones(src_data.shape, dtype=bool)
        # En estos TIFF: 1 = sombra y 0 = iluminado.
        sun_pixels = int(np.count_nonzero((src_data == 0) & valid))
        shadow_pixels = int(np.count_nonzero((src_data == 1) & valid))
        pixel_area_m2 = abs(src.transform.a * src.transform.e - src.transform.b * src.transform.d)

        transform, default_width, default_height = calculate_default_transform(
            src.crs, "EPSG:4326", src.width, src.height, *src.bounds
        )
        scale = min(1.0, max_size / max(default_width, default_height))
        width = max(1, int(default_width * scale))
        height = max(1, int(default_height * scale))
        transform = transform * transform.scale(
            default_width / width, default_height / height
        )

        projected = np.full((height, width), 255, dtype=np.uint8)
        reproject(
            source=src_data,
            destination=projected,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=transform,
            dst_crs="EPSG:4326",
            dst_nodata=255,
            resampling=Resampling.nearest,
        )

        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[projected == 0] = [30, 170, 80, 210]
        rgba[projected == 1] = [220, 55, 45, 220]
        image_buffer = BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(image_buffer, format="PNG", optimize=True)
        image_url = "data:image/png;base64," + base64.b64encode(image_buffer.getvalue()).decode("ascii")

        west = transform.c
        north = transform.f
        east = west + transform.a * width
        south = north + transform.e * height
        return image_url, [[south, west], [north, east]], sun_pixels, shadow_pixels, pixel_area_m2

def main():
    barrios_gdf = load_barrios()
    
    with st.sidebar:
        st.header("Configuración")
        
        if barrios_gdf is not None:
            # Assuming there's a column 'NOMBRE' or 'nombre' for the neighborhood name.
            name_cols = [c for c in barrios_gdf.columns if 'nombre' in c.lower() or 'name' in c.lower()]
            name_col = name_cols[0] if name_cols else barrios_gdf.columns[0]
            
            barrio_list = sorted(barrios_gdf[name_col].dropna().unique())
            selected_barrio = st.selectbox(
                "Selecciona una vista",
                ["Todo Medellín"] + barrio_list,
                help="Elige Todo Medellín para ver la ciudad completa o un barrio/vereda para analizar sus techos."
            )
        else:
            st.error("No se pudo cargar el archivo de barrios.")
            selected_barrio = None
            
        st.subheader("Parámetros del Panel")
        efficiency = st.number_input("Eficiencia del Panel (%)", min_value=1.0, max_value=100.0, value=20.0, step=1.0) / 100.0
        pvgis_yield = st.number_input("Generación Específica PVGIS (kWh/kWp/año)", min_value=500.0, max_value=2500.0, value=1415.21, step=10.0, help="Dato de PVGIS para Medellín: 1 kWp genera 1415.21 kWh anuales.")

        st.subheader("Parámetros de Sombra")
        dia_sel = st.selectbox("Fecha", ["21 de Junio", "21 de Diciembre"])
        hora_sel = st.selectbox("Hora", ["08:00", "12:00", "16:00"])
        
        date_str = "06_21" if "Junio" in dia_sel else "12_21"
        hour_str = "08h" if "08" in hora_sel else "12h" if "12" in hora_sel else "16h"
        tif_filename = f"medellin_techo_sombra_2026_{date_str}_{hour_str}.tif"
        tif_path = os.path.join(".", tif_filename)

    if selected_barrio == "Todo Medellín" and barrios_gdf is not None:
        st.write("### Vista general de Medellín")
        st.caption("La capa muestra los píxeles de techo con sol y sombra para la fecha y hora seleccionadas.")

        barrios_map = barrios_gdf.copy()
        if barrios_map.crs and barrios_map.crs.to_epsg() != 4326:
            barrios_map = barrios_map.to_crs(epsg=4326)

        city_bounds = barrios_map.total_bounds
        city_center = barrios_map.geometry.union_all().centroid
        m = folium.Map(
            location=[city_center.y, city_center.x],
            zoom_start=11,
            tiles="CartoDB positron"
        )

        if os.path.exists(tif_path):
            with st.spinner("Preparando la capa de sombras de Medellín..."):
                overlay_url, overlay_bounds, sun_pixels, shadow_pixels, pixel_area_m2 = load_city_shadow_overlay(tif_path)
            folium.raster_layers.ImageOverlay(
                image=overlay_url,
                bounds=overlay_bounds,
                name="Sol y sombra en techos",
                opacity=0.82,
                interactive=True,
                cross_origin=False,
                zindex=2,
            ).add_to(m)

            roof_pixels = sun_pixels + shadow_pixels
            sun_pct = (sun_pixels / roof_pixels * 100) if roof_pixels else 0
            shadow_pct = (shadow_pixels / roof_pixels * 100) if roof_pixels else 0
            metric1, metric2, metric3 = st.columns(3)
            metric1.metric("Cobertura con sol", f"{sun_pct:.1f}%")
            metric2.metric("Cobertura con sombra", f"{shadow_pct:.1f}%")
            metric3.metric("Píxeles de techo analizados", f"{roof_pixels:,}")

            # Usa los mismos supuestos del análisis individual por barrio.
            roof_area_m2 = roof_pixels * pixel_area_m2
            effective_solar_factor = sun_pixels / roof_pixels if roof_pixels else 0
            city_energy_kwh = roof_area_m2 * efficiency * pvgis_yield * effective_solar_factor
            city_homes = city_energy_kwh / 1560
            city_co2_tons = city_energy_kwh * 0.097 / 1000

            st.write("---")
            st.subheader("Impacto Social y Ambiental Estimado")
            impact1, impact2, impact3 = st.columns(3)
            with impact1:
                st.info(
                    f"Hogares Equivalentes:\n\n~**{city_homes:,.0f}** hogares podrían cubrir "
                    "su consumo eléctrico anual (basado en un consumo promedio de 130 kWh/mes)."
                )
            with impact2:
                st.success(
                    f"CO₂ Evitado:\n\n~**{city_co2_tons:,.1f}** toneladas de CO₂ evitadas "
                    "al año (factor de emisión UPME para Colombia de 0.097 kg CO₂/kWh)."
                )
            with impact3:
                st.warning(
                    f"Impacto de Sombras:\n\nLas sombras reducen el potencial en un "
                    f"**{shadow_pct:.1f}%** promedio en este escenario "
                    f"(efectividad real: **{sun_pct:.1f}%**)."
                )
            st.write("---")

            legend = """
            <div style="position: fixed; bottom: 35px; left: 55px; z-index: 9999;
                        background: white; padding: 10px 14px; border: 1px solid #777;
                        border-radius: 4px; font-size: 14px;">
              <b>Condición del techo</b><br>
              <span style="color:#1eaa50; font-size:20px;">■</span> Con sol<br>
              <span style="color:#dc372d; font-size:20px;">■</span> Con sombra
            </div>
            """
            m.get_root().html.add_child(folium.Element(legend))

        folium.GeoJson(
            barrios_map[[name_col, "geometry"]].to_json(),
            name="Barrios y veredas",
            style_function=lambda feature: {
                "fillColor": "transparent",
                "color": "#333333",
                "weight": 0.8,
                "fillOpacity": 0,
            },
            highlight_function=lambda feature: {
                "fillColor": "#ffd166",
                "color": "#111111",
                "weight": 2,
                "fillOpacity": 0.45,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=[name_col],
                aliases=["Barrio/Vereda:"]
            ),
        ).add_to(m)

        m.fit_bounds([
            [city_bounds[1], city_bounds[0]],
            [city_bounds[3], city_bounds[2]],
        ])
        folium.LayerControl(collapsed=False).add_to(m)
        st_folium(
            m,
            width=1200,
            height=650,
            returned_objects=[],
            key="map_todo_medellin",
        )
        return

    if selected_barrio and barrios_gdf is not None:
        # Get the geometry of the selected barrio
        barrio_row = barrios_gdf[barrios_gdf[name_col] == selected_barrio].iloc[0]
        barrio_geom = barrio_row.geometry
        
        st.write(f"### Analizando: {selected_barrio}")
        with st.spinner("Cargando edificaciones..."):
            polygons = load_polygons(selected_barrio, barrio_geom, barrios_gdf.crs)
            if polygons is not None:
                polygons = polygons.copy()
            
        if polygons is not None and not polygons.empty:
            st.write(f"Se encontraron **{len(polygons)}** edificaciones en este barrio.")
            
            # Simple simulation column
            if polygons.crs and polygons.crs.is_geographic:
                polygons['area_m2'] = polygons.to_crs(epsg=3857).geometry.area
            else:
                polygons['area_m2'] = polygons.geometry.area
                
            with st.spinner(f"Calculando sombras para {len(polygons)} techos..."):
                polygons = calculate_shadows(polygons, tif_path)
            
            # Cálculo usando PVGIS: 
            # Capacidad Instalada (kWp) = Área (m2) * Eficiencia
            polygons['capacidad_kwp'] = polygons['area_m2'] * efficiency
            # Energía Anual = Capacidad * Yield de PVGIS * Factor Sombra
            polygons['energia_anual_kwh'] = polygons['capacidad_kwp'] * pvgis_yield * polygons['shadow_factor']
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Edificaciones", len(polygons))
            col2.metric("Energía Total (kWh/año)", f"{polygons['energia_anual_kwh'].sum():,.0f}")
            col3.metric("Promedio por Techo (kWh/año)", f"{polygons['energia_anual_kwh'].mean():,.0f}")
            
            # --- SECCIÓN DE RESUMEN E INTERPRETACIÓN ---
            st.write("---")
            st.subheader(" Impacto Social y Ambiental Estimado")
            
            # Métricas agregadas
            energia_total = polygons['energia_anual_kwh'].sum()
            area_total = polygons['area_m2'].sum()
            zone_shadow_stats = calculate_zone_shadow_stats(
                tif_path, barrio_geom, barrios_gdf.crs
            )
            if zone_shadow_stats:
                shadow_pct_zone = zone_shadow_stats['shadow_pct']
                solar_pct_zone = zone_shadow_stats['sun_pct']
            else:
                # Respaldo ponderado por área si el raster no contiene píxeles válidos.
                solar_factor = np.average(
                    polygons['shadow_factor'], weights=polygons['area_m2']
                ) if area_total > 0 else 0
                solar_pct_zone = solar_factor * 100
                shadow_pct_zone = (1 - solar_factor) * 100
            
            # Consumo residencial promedio en Medellín es de aprox 130 kWh/mes (1560 kWh/año)
            hogares = energia_total / 1560
            # Factor de emisión de CO2 para el SIN de Colombia (aprox 0.097 kg CO2/kWh de la UPME)
            co2 = (energia_total * 0.097) / 1000
            
            col_imp1, col_imp2, col_imp3 = st.columns(3)
            with col_imp1:
                st.info(f"Hogares Equivalentes:\n\n~**{hogares:,.0f}** hogares podrían cubrir su consumo eléctrico anual (basado en un consumo promedio de 130 kWh/mes).")
            with col_imp2:
                st.success(f"CO₂ Evitado:\n\n~**{co2:,.1f}** toneladas de CO₂ evitadas al año (factor de emisión UPME para Colombia de 0.097 kg CO₂/kWh).")
            with col_imp3:
                st.warning(f"Impacto de Sombras:\n\nLas sombras cubren un **{shadow_pct_zone:.1f}%** de los píxeles de techo en este escenario (superficie iluminada: **{solar_pct_zone:.1f}%**).")
                
            st.write("---")
            col_chart, col_text = st.columns([2, 1])
            
            with col_chart:
                st.subheader("Potencial por Tamaño de Techo")
                
                # Clasificación de techos según tamaño
                def categorizar_techo(area):
                    if area < 50:
                        return '1. Pequeño (<50 m²)'
                    elif area < 150:
                        return '2. Mediano (50-150 m²)'
                    elif area < 500:
                        return '3. Grande (150-500 m²)'
                    else:
                        return '4. Industrial/Inst. (>500 m²)'
                
                # Convertir a Categorical para mantener el orden natural de las categorías
                order = ['1. Pequeño (<50 m²)', '2. Mediano (50-150 m²)', '3. Grande (150-500 m²)', '4. Industrial/Inst. (>500 m²)']
                polygons['categoria'] = pd.Categorical(
                    polygons['area_m2'].apply(categorizar_techo),
                    categories=order,
                    ordered=True
                )
                
                # Agrupación por categoría (se mantienen todas gracias a observed=False)
                df_cat = polygons.groupby('categoria', observed=False).agg(
                    energia_total=('energia_anual_kwh', 'sum'),
                    cantidad=('geometry', 'count')
                ).reset_index()
                
                df_cat_plot = df_cat.copy()
                df_cat_plot['Energía MWh/año'] = df_cat_plot['energia_total'] / 1000  # Convertir a MWh
                df_cat_plot = df_cat_plot.rename(columns={'categoria': 'Categoría de Techo', 'cantidad': 'Cantidad de Edificaciones'})
                
                # Convertir la columna categórica a string para evitar problemas de serialización en Vega-Lite/Streamlit
                df_cat_plot['Categoría de Techo'] = df_cat_plot['Categoría de Techo'].astype(str)
                
                # Intentar usar Plotly, si no st.bar_chart nativo
                try:
                    import plotly.express as px
                    fig = px.bar(
                        df_cat_plot, 
                        x='Categoría de Techo', 
                        y='Energía MWh/año',
                        color='Categoría de Techo',
                        hover_data=['Cantidad de Edificaciones'],
                        color_discrete_sequence=px.colors.qualitative.Pastel
                    )
                    fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=320)
                    st.plotly_chart(fig, use_container_width=True)
                except Exception:
                    # Usar st.bar_chart con parámetros x e y explícitos sobre el DataFrame con strings
                    st.bar_chart(df_cat_plot, x='Categoría de Techo', y='Energía MWh/año')
                    
            with col_text:
                st.subheader("Interpretación")
                if not df_cat.empty:
                    max_cat_row = df_cat.loc[df_cat['energia_total'].idxmax()]
                    max_cat_name = max_cat_row['categoria']
                    max_cat_energy = max_cat_row['energia_total'] / 1000  # MWh
                    max_cat_pct = (max_cat_row['energia_total'] / energia_total) * 100 if energia_total > 0 else 0
                    
                    st.markdown(f"""
                    Analizando los techos de **{selected_barrio}**:
                    
                    * **Mayor Aporte:** Los techos **{max_cat_name[3:]}** representan la mayor oportunidad de generación, aportando **{max_cat_energy:,.1f} MWh/año** (el **{max_cat_pct:.1f}%** del total del barrio).
                    * **Área Aprovechable:** Se analizó un área acumulada de techos de **{area_total:,.0f} m²**.
                    * **Configuración Solar:** Con paneles al **{efficiency*100:.0f}%** de eficiencia y generación específica de **{pvgis_yield:,.1f} kWh/kWp/año**, Medellín puede avanzar hacia la descentralización de su matriz energética.
                    """)
                else:
                    st.write("No hay datos disponibles para el análisis detallado.")
                    
            st.write("---")
            
            # Map
            st.subheader("Mapa de Potencial Fotovoltaico")
            
            centroid = barrio_geom.centroid
            # Ensure centroid is lat/lon for folium
            if polygons.crs and not polygons.crs.is_geographic:
                 centroid = gpd.GeoSeries([centroid], crs=polygons.crs).to_crs(epsg=4326).iloc[0]
                 
            m = folium.Map(location=[centroid.y, centroid.x], zoom_start=15, tiles="CartoDB positron")
            
            # Add barrio boundary
            folium.GeoJson(
                barrio_row.geometry if barrios_gdf.crs.is_geographic else gpd.GeoSeries([barrio_row.geometry], crs=barrios_gdf.crs).to_crs(epsg=4326).iloc[0],
                style_function=lambda x: {'fillColor': 'transparent', 'color': 'black', 'weight': 2}
            ).add_to(m)
            
            if len(polygons) > 3000:
                st.warning("Se muestran solo las 3000 edificaciones más grandes.")
                polygons_to_show = polygons.sort_values('area_m2', ascending=False).head(3000)
            else:
                polygons_to_show = polygons
                
            # Convert to EPSG 4326 for folium unconditionally
            try:
                polygons_to_show = polygons_to_show.to_crs(epsg=4326)
            except Exception:
                pass
                
            import branca.colormap as cm
            if polygons_to_show['energia_anual_kwh'].max() > polygons_to_show['energia_anual_kwh'].min():
                colormap = cm.LinearColormap(colors=['red', 'yellow', 'green'], 
                                           vmin=polygons_to_show['energia_anual_kwh'].min(), 
                                           vmax=polygons_to_show['energia_anual_kwh'].max())
            else:
                colormap = cm.LinearColormap(colors=['green', 'green'], vmin=0, vmax=1)
            
            def style_func(feature):
                pot = feature['properties'].get('energia_anual_kwh', 0)
                return {
                    'fillColor': colormap(pot) if pot else 'gray',
                    'color': 'black',
                    'weight': 0.5,
                    'fillOpacity': 0.7
                }
                
            folium.GeoJson(
                polygons_to_show[['energia_anual_kwh', 'area_m2', 'geometry']].to_json(),
                style_function=style_func,
                tooltip=folium.GeoJsonTooltip(fields=['energia_anual_kwh', 'area_m2'], aliases=['Energía Anual (kWh):', 'Área (m2):'])
            ).add_to(m)
            
            colormap.add_to(m)
            
            st_folium(m, width=1200, height=600, returned_objects=[], key=f"map_{selected_barrio}_{tif_filename}_{efficiency}")
        else:
            st.warning("No se encontraron edificaciones.")

if __name__ == "__main__":
    main()
