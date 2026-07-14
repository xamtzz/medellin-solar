import dash
from dash import dcc, html, Input, Output
import pandas as pd
import plotly.express as px
import os
from mapa_interactivo import crear_mapa_base

# 1. Cargar el dataset ligero (el CSV generado previamente en tu notebook)
# Subimos un nivel de carpeta ('..') para buscar en 'datos/'
ruta_csv = os.path.abspath(os.path.join(os.path.dirname(__name__), "..", "datos", "area_techos_medellin_resumen.csv"))

try:
    df_resumen = pd.read_csv(ruta_csv)
    barrios_disponibles = df_resumen['nombre'].unique()
except FileNotFoundError:
    # Datos de respaldo por si aún no has generado el CSV
    df_resumen = pd.DataFrame({'nombre': ['Sin datos'], 'area_total_techos_m2': [0]})
    barrios_disponibles = ['Sin datos']

# 2. Inicializar la aplicación Dash
app = dash.Dash(__name__)

# 3. Construir la interfaz de usuario (Layout)
app.layout = html.Div(style={'fontFamily': 'Arial, sans-serif', 'padding': '20px'}, children=[
    
    html.H1("Dashboard: Potencial de Techos en Medellín 🇨🇴", style={'textAlign': 'center', 'color': '#2C3E50'}),
    
    # Panel de Controles
    html.Div(style={'width': '40%', 'margin': 'auto', 'paddingBottom': '30px'}, children=[
        html.Label("Selecciona un Barrio para analizar:", style={'fontWeight': 'bold'}),
        dcc.Dropdown(
            id='dropdown-barrio',
            options=[{'label': barrio, 'value': barrio} for barrio in barrios_disponibles],
            value=barrios_disponibles[0] if len(barrios_disponibles) > 0 else None,
            clearable=False
        )
    ]),
    
    # Contenedor para Gráfica y Mapa
    html.Div(style={'display': 'flex', 'flexDirection': 'row', 'gap': '20px'}, children=[
        
        # Columna Izquierda: Gráfica de barras
        html.Div(style={'width': '50%'}, children=[
            dcc.Graph(id='grafico-area')
        ]),
        
        # Columna Derecha: Mapa de Folium
        html.Div(style={'width': '50%'}, children=[
            html.H3("Visualización Espacial", style={'textAlign': 'center', 'color': '#2C3E50'}),
            # El mapa de Folium se inyecta aquí como un documento HTML embebido
            html.Iframe(id='mapa-folium', srcDoc='', width='100%', height='450px', style={'border': 'none', 'borderRadius': '10px', 'boxShadow': '0px 4px 6px rgba(0,0,0,0.1)'})
        ])
    ])
])

# 4. Lógica de interactividad (Callbacks)
@app.callback(
    Output('grafico-area', 'figure'),
    Output('mapa-folium', 'srcDoc'),
    Input('dropdown-barrio', 'value')
)
def actualizar_dashboard(barrio_seleccionado):
    # Filtrar el DataFrame por el barrio seleccionado
    df_filtrado = df_resumen[df_resumen['nombre'] == barrio_seleccionado]
    
    # Construir la gráfica con Plotly
    fig = px.bar(
        df_filtrado, 
        x='nombre', 
        y='area_total_techos_m2', 
        title=f'Área útil acumulada en: {barrio_seleccionado}',
        labels={'area_total_techos_m2': 'Área Total (m²)', 'nombre': 'Barrio'},
        color_discrete_sequence=['#27AE60']
    )
    fig.update_layout(plot_bgcolor='white')
    
    # Construir el mapa de Folium y renderizarlo a HTML
    mapa = crear_mapa_base()
    mapa_html = mapa.get_root().render()
    
    return fig, mapa_html

# 5. Ejecución del servidor
if __name__ == '__main__':
    app.run(debug=True)