import folium

def crear_mapa_base():
    # Inicializamos el mapa centrado en las coordenadas de Medellín
    # Usamos un estilo de mapa claro (cartodbpositron) para que resalten los datos
    mapa = folium.Map(
        location=[6.2442, -75.5812], 
        zoom_start=12, 
        tiles="cartodbpositron"
    )
    
    # Aquí, más adelante, agregaremos la capa de GeoJSON de los techos o barrios
    # Por ahora devolvemos el lienzo base
    return mapa