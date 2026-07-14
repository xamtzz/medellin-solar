import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import st_folium
import rasterio
from rasterio.mask import mask
import os
import numpy as np
from shapely.geometry import box

st.set_page_config(page_title="Potencial Fotovoltaico Medellín", page_icon="☀️", layout="wide")

st.title("☀️ Visualizador de Potencial Fotovoltaico en Medellín")
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
                    # Si el TIF tiene 1 como sombra y 0 como sol, el factor de radiación sería 1 - mean
                    # Asumiremos que mean_val es el factor (0 a 1). Ajustable según el TIF real.
                    mean_val = np.mean(valid_data)
                    shadow_factors.append(mean_val)
                else:
                    shadow_factors.append(0.8)
            except Exception:
                shadow_factors.append(0.8)
                
    polys['shadow_factor'] = shadow_factors
    return polys

def main():
    barrios_gdf = load_barrios()
    
    with st.sidebar:
        st.header("Configuración")
        
        if barrios_gdf is not None:
            # Assuming there's a column 'NOMBRE' or 'nombre' for the neighborhood name.
            name_cols = [c for c in barrios_gdf.columns if 'nombre' in c.lower() or 'name' in c.lower()]
            name_col = name_cols[0] if name_cols else barrios_gdf.columns[0]
            
            barrio_list = sorted(barrios_gdf[name_col].dropna().unique())
            selected_barrio = st.selectbox("Selecciona un Barrio/Vereda", barrio_list)
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
