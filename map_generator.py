import os
import folium
import numpy as np
import networkx as nx
import osmnx as ox
import geopandas as gpd
from shapely.geometry import Point

ox.settings.use_cache = True
ox.settings.log_console = True

ROAD_TYPES_TO_DOWLOAD = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
}

OUTPUT_FILE_NAME = "test_map.html"
DOWNLOAD_MODE = False


def download_roads():
    def edge_is_major(row) -> bool:
        hw = row.get("highway", None)
        if hw is None:
            return False
        if isinstance(hw, str):
            return hw in ROAD_TYPES_TO_DOWLOAD
        return any(h in ROAD_TYPES_TO_DOWLOAD for h in hw)

    graph = ox.graph_from_place("Armenia", network_type="drive")
    nodes, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True)
    filtered_edges = edges[edges.apply(edge_is_major, axis=1)]
    edge_keys = list(filtered_edges.index)
    graph_filtered = graph.edge_subgraph(edge_keys).copy()
    nodes_filtered, edges_filtered = ox.graph_to_gdfs(graph_filtered, nodes=True, edges=True)
    return graph_filtered, nodes_filtered, edges_filtered


def load_graph(path):
    graph = ox.load_graphml(path)
    nodes, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True)
    return graph, nodes, edges


def download_charging_stations():
    return ox.features_from_place("Armenia", tags={"amenity": "charging_station"})


def create_map(nodes, edges, stations):
    mean_latitude = float(nodes["y"].mean())
    mean_longnitude = float(nodes["x"].mean())
    map = folium.Map(location=[mean_latitude, mean_longnitude], tiles="OpenStreetMap")
    folium.GeoJson(edges[["geometry"]], name="Armenia Road Network", tooltip=folium.GeoJsonTooltip(fields=[]),).add_to(map)
    for _, row in stations.iterrows():
        geom = row.geometry
        if isinstance(geom, Point):
            lat = geom.y
            lon = geom.x
            name = row.get("name", "EV charger")

            folium.CircleMarker(
                location=[lat, lon],
                radius=4,
                color="lightgreen",
                fill=True,
                fill_opacity=0.9,
                popup=name,
            ).add_to(map)

    folium.LayerControl().add_to(map)
    return map


def create_path(map, graph, nodes, stations):
    stations_df = stations[stations.geometry.type == "Point"].copy()
    s1 = stations_df.iloc[0]
    s2 = stations_df.iloc[1]
    lon1, lat1 = s1.geometry.x, s1.geometry.y
    lon2, lat2 = s2.geometry.x, s2.geometry.y

    from_node, to_node = ox.distance.nearest_nodes(
        graph, X=[lon1, lon2], Y=[lat1, lat2]
    )
    route = nx.shortest_path(graph, source=from_node, target=to_node, weight="length")
    route_coords = [(nodes.loc[n].y, nodes.loc[n].x) for n in route]
    folium.PolyLine(
        locations=route_coords,
        weight=5,
        color="orange",
        opacity=0.8,
        tooltip="Path",
    ).add_to(map)


def add_travel_time_to_nodes(graph, default_speed_limit=60):
    for u, v, k, data in graph.edges(keys=True, data=True):
        length = data.get("length", 0.0)
        maxspeed = data.get("maxspeed", None)

        if isinstance(maxspeed, list):
            maxspeed = maxspeed[0]
        
        if maxspeed is None:
            speed_limit = default_speed_limit
        else:
            # try:
            speed_limit = float(str(maxspeed).split()[0])
            # except:
            #     speed_limit = default_speed_limit
        
        speed_limit_mps = speed_limit / 3.6
        if speed_limit_mps:
            raise ValueError(f"speed limit is {speed_limit_mps} at node")
        
        data["energy_consumption"] = length / speed_limit_mps


def add_energy_consumption_to_nodes(graph, default_energy_consumption=0.25):
    """Enrgy consupotion is % of battery per kilometer"""
    #TODO add more complex calculations, check if there are any 2-link nodes
    for u, v, k, data in graph.edges(keys=True, data=True):
        length = data.get("length", 0.0)
        data["travel_time"] = length * default_energy_consumption * 0.001


def expand_graph(graph, stations_df):
    H = nx.DiGraph()
    battery_capacity = 100  # TODO convert to Kw/h
    charging_nodes = set()
    for _, row in stations_df.iterrows():
        if row.geometry.geom_type != "Point":
            continue
        lon, lat = row.geometry.x, row.geometry.y
        node = ox.distance.nearest_nodes(graph, X=[lon], Y=[lat])[0]
        charging_nodes.add(node)
    add_travel_time_to_nodes(graph, default_speed_kph=60)
    add_energy_consumption_to_nodes(graph)
    for u, v, key, data in graph.edges(keys=True, data=True):
        energy = data["energy"]
        travel_time = data["travel_time"]
        # discretizations
        # TODO add good system for discretization, maybe should be adaptive to the length of graph
        delta_k = int(np.ceil(energy / battery_capacity))
        if delta_k <= 0:
            delta_k = 1

        for k in range(delta_k, battery_capacity + 1):
            src = (u, k)
            dst = (v, k - delta_k)
            H.add_edge(src, dst, time=travel_time)
    
    charge_step = 1  
    charge_time_per_step = 10
    for v in charging_nodes:
        for k in range(0, battery_capacity - charge_step + 1):
            src = (v, k)
            dst = (v, k + charge_step)
            H.add_edge(src, dst, time=charge_time_per_step)



def main():
    save_path_map = os.path.join("maps", OUTPUT_FILE_NAME)
    save_path_graph = os.path.join("graphs", "test_graph.graphml")
    save_path_stations = os.path.join("graphs", "test_chargers_stations.geojson")

    if DOWNLOAD_MODE:
        graph, nodes, edges = download_roads()
        stations = download_charging_stations()
        ox.save_graphml(graph, save_path_graph)
        stations.to_file(save_path_stations, driver="GeoJSON")
    
    else:
        graph, nodes, edges = load_graph(save_path_graph)
        stations = gpd.read_file(save_path_stations)

    fmap = create_map(nodes, edges, stations)
    create_path(fmap, graph, nodes, stations)


    fmap.save(save_path_map)
    print(f"saved to: {os.path.abspath(save_path_map)}")


if __name__ == "__main__":
    main()
