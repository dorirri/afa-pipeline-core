"""Geo-timeline analysis: travel segments, location clusters and anomalies.

Ported from ``analysis/diplomka/geo_timeline_analyzer.py`` of the original AFA
prototype with the algorithms, thresholds and HTML/PNG/map outputs unchanged.
Changes made in the port, each marked ``AFA port``:

* The three anomaly rules (speed above 300 km/h, a gap of more than 30 days,
  photos between 23:00 and 05:59) were moved unchanged from
  ``_create_travel_pattern_report`` into ``_detect_anomalies`` so the adapter can
  report them as findings.

Use it through :mod:`afa_pipeline.geo_timeline` (``afa geo-timeline``), which
builds the metadata file it expects from an ``afa analyze`` report.
"""

#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime

import folium
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from folium.plugins import MarkerCluster
from geopy.distance import geodesic
from sklearn.cluster import DBSCAN

MIN_LOCATION_CHANGE_KM = 5.0
LOCATION_CLUSTER_RADIUS_KM = 15.0
SIGNIFICANT_SPEED_KMH = 60.0
TIME_WINDOW_HOURS = 12
MIN_SUSPICIOUS_TIME_GAP_DAYS = 30.0


class GeoTimelineAnalyzer:
    def __init__(self, metadata_path):
        self.metadata_path = metadata_path
        self.metadata_df = self._load_metadata()
        self.output_dir = None
        self.travel_segments = []
        self.location_clusters = []
        self.time_location_clusters = []

    def _load_metadata(self):
        if self.metadata_path.lower().endswith(".json"):
            with open(self.metadata_path) as f:
                data = json.load(f)
                df = pd.DataFrame(data)
        elif self.metadata_path.lower().endswith(".csv"):
            df = pd.read_csv(self.metadata_path)
        else:
            raise ValueError("Unsupported file format. Use JSON or CSV.")

        if "DateTime" in df.columns:
            try:
                for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        df["DateTimeObj"] = pd.to_datetime(
                            df["DateTime"], format=fmt, errors="coerce"
                        )
                        break
                    except:
                        continue
            except Exception as e:
                print(f"Error converting DateTime: {e!s}")

        if "Coordinates" in df.columns and isinstance(df["Coordinates"].iloc[0], str):
            df["Coordinates"] = df["Coordinates"].apply(self._parse_coordinates)

        if "Coordinates" in df.columns:
            df["Latitude"] = df["Coordinates"].apply(
                lambda x: x[0] if isinstance(x, (list, tuple)) and len(x) >= 2 else None
            )
            df["Longitude"] = df["Coordinates"].apply(
                lambda x: x[1] if isinstance(x, (list, tuple)) and len(x) >= 2 else None
            )

        if "DateTimeObj" in df.columns:
            df = df.sort_values("DateTimeObj").reset_index(drop=True)

        if "DateTimeObj" in df.columns:
            df["TimeDelta"] = df["DateTimeObj"].diff().dt.total_seconds()

        return df

    def _parse_coordinates(self, coord_str):
        if pd.isna(coord_str) or not coord_str:
            return None

        try:
            if isinstance(coord_str, str):
                if "," in coord_str:
                    parts = coord_str.strip("()[]").split(",")
                    return (float(parts[0].strip()), float(parts[1].strip()))
                else:
                    return None
            elif isinstance(coord_str, (list, tuple)) and len(coord_str) >= 2:
                return (coord_str[0], coord_str[1])
            else:
                return None
        except:
            return None

    def analyze(self, output_dir):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        geo_df = self.metadata_df.dropna(subset=["Latitude", "Longitude"])

        if len(geo_df) < 2:
            print("Not enough geotagged images for travel analysis")
            return None

        self.travel_segments = self._identify_travel_patterns(geo_df)
        self._save_travel_segments()

        self.location_clusters = self._cluster_locations(geo_df)

        self._create_visualizations()

        return {
            "travel_segments": self.travel_segments,
            "location_clusters": self.location_clusters,
            "time_location_clusters": self.time_location_clusters,
        }

    def _identify_travel_patterns(self, df):
        if len(df) < 2:
            return []

        travel_segments = []
        current_segment = None
        MIN_TIME_GAP_HOURS = 12

        for i in range(1, len(df)):
            prev_row = df.iloc[i - 1]
            curr_row = df.iloc[i]

            time_diff_hours = (
                curr_row["DateTimeObj"] - prev_row["DateTimeObj"]
            ).total_seconds() / 3600

            prev_coords = (prev_row["Latitude"], prev_row["Longitude"])
            curr_coords = (curr_row["Latitude"], curr_row["Longitude"])
            try:
                distance_km = geodesic(prev_coords, curr_coords).kilometers
            except:
                distance_km = 0

            speed_kmh = distance_km / time_diff_hours if time_diff_hours > 0 else 0

            segment_break = False

            if time_diff_hours > MIN_TIME_GAP_HOURS:
                segment_break = True

            if distance_km >= MIN_LOCATION_CHANGE_KM:
                if current_segment is None:
                    current_segment = {
                        "start_idx": i - 1,
                        "start_time": prev_row["DateTimeObj"],
                        "start_location": prev_coords,
                        "points": [prev_coords, curr_coords],
                        "photo_indices": [i - 1, i],
                        "distance_km": distance_km,
                        "speeds": [speed_kmh],
                    }
                else:
                    current_segment["points"].append(curr_coords)
                    current_segment["photo_indices"].append(i)
                    current_segment["distance_km"] += distance_km
                    current_segment["speeds"].append(speed_kmh)
            else:
                segment_break = True

            if segment_break and current_segment is not None:
                self._finalize_segment(current_segment, prev_row, df)
                travel_segments.append(current_segment)
                current_segment = None

        if current_segment is not None:
            self._finalize_segment(current_segment, df.iloc[-1], df)
            travel_segments.append(current_segment)

        return travel_segments

    def _finalize_segment(self, segment, last_row, df):
        """Helper to calculate segment metrics."""
        segment["end_idx"] = segment["photo_indices"][-1]
        segment["end_time"] = last_row["DateTimeObj"]
        segment["end_location"] = (last_row["Latitude"], last_row["Longitude"])
        segment["avg_speed"] = np.mean(segment["speeds"]) if segment["speeds"] else 0
        segment["max_speed"] = max(segment["speeds"]) if segment["speeds"] else 0
        segment["duration_hours"] = (
            segment["end_time"] - segment["start_time"]
        ).total_seconds() / 3600
        segment["num_photos"] = len(segment["photo_indices"])
        segment["transport_type"] = self._classify_transport(segment["avg_speed"])

        start_end_dist = geodesic(segment["start_location"], segment["end_location"]).kilometers
        segment["directness_ratio"] = (
            start_end_dist / segment["distance_km"] if segment["distance_km"] > 0 else 1
        )

    def _classify_transport(self, speed_kmh):
        if speed_kmh < 7:
            return "Walking"
        elif speed_kmh < 20:
            return "Cycling/Urban"
        elif speed_kmh < 90:
            return "Car/Bus"
        elif speed_kmh < 300:
            return "Train"
        else:
            return "Flight"

    def _cluster_locations(self, df):
        if len(df) < 2:
            return []

        coords = df[["Latitude", "Longitude"]].values

        earth_radius = 6371.0
        epsilon = LOCATION_CLUSTER_RADIUS_KM / earth_radius

        db = DBSCAN(eps=epsilon, min_samples=1, algorithm="ball_tree", metric="haversine")
        db.fit(np.radians(coords))

        cluster_labels = db.labels_
        df_copy = df.copy()
        df_copy["cluster"] = cluster_labels

        clusters = []
        for cluster_id in set(cluster_labels):
            cluster_df = df_copy[df_copy["cluster"] == cluster_id]

            centroid = (cluster_df["Latitude"].mean(), cluster_df["Longitude"].mean())

            max_dist = 0
            for _, row in cluster_df.iterrows():
                dist = geodesic(centroid, (row["Latitude"], row["Longitude"])).kilometers
                max_dist = max(max_dist, dist)

            if "DateTimeObj" in cluster_df.columns:
                start_time = cluster_df["DateTimeObj"].min()
                end_time = cluster_df["DateTimeObj"].max()
                duration = (end_time - start_time).total_seconds() / 3600
            else:
                start_time = None
                end_time = None
                duration = None

            clusters.append(
                {
                    "cluster_id": int(cluster_id),
                    "centroid": centroid,
                    "radius_km": max_dist,
                    "num_photos": len(cluster_df),
                    "photo_indices": cluster_df.index.tolist(),
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_hours": duration,
                    "filenames": cluster_df["FileName"].tolist()
                    if "FileName" in cluster_df.columns
                    else [],
                    "cameras": cluster_df["CameraModel"].unique().tolist()
                    if "CameraModel" in cluster_df.columns
                    else [],
                }
            )

        return clusters

    def _generate_cluster_map_images(self):
        if not self.location_clusters:
            return

        for i, cluster in enumerate(self.location_clusters):
            m = folium.Map(location=cluster["centroid"], zoom_start=12, tiles="OpenStreetMap")

            folium.Circle(
                location=cluster["centroid"],
                radius=cluster["radius_km"] * 1000,
                color="blue",
                fill=True,
                fill_opacity=0.3,
                popup=f"Cluster {i + 1}: {cluster['num_photos']} photos",
            ).add_to(m)

            cluster_photos = self.metadata_df.iloc[cluster["photo_indices"]]
            for idx, row in cluster_photos.iterrows():
                if pd.notna(row["Latitude"]) and pd.notna(row["Longitude"]):
                    folium.Marker(
                        [row["Latitude"], row["Longitude"]],
                        popup=f"Photo: {row['FileName']}",
                        icon=folium.Icon(icon="camera", prefix="fa", size="small"),
                    ).add_to(m)

            cluster_map_path = os.path.join(self.output_dir, f"cluster_{i + 1}_map.html")
            m.save(cluster_map_path)

    def _save_travel_segments(self):
        if not self.travel_segments:
            return

        serializable_segments = []
        for segment in self.travel_segments:
            serializable_segment = {
                "start_time": segment["start_time"].isoformat()
                if segment["start_time"] is not None
                else None,
                "end_time": segment["end_time"].isoformat()
                if segment["end_time"] is not None
                else None,
                "start_location": segment["start_location"],
                "end_location": segment["end_location"],
                "distance_km": segment["distance_km"],
                "avg_speed": segment["avg_speed"],
                "max_speed": segment["max_speed"],
                "duration_hours": segment["duration_hours"],
                "num_photos": segment["num_photos"],
                "transport_type": segment["transport_type"],
                "directness_ratio": segment["directness_ratio"],
                "photo_indices": segment["photo_indices"],
            }
            serializable_segments.append(serializable_segment)

        travel_path = os.path.join(self.output_dir, "travel_segments.json")
        with open(travel_path, "w") as f:
            json.dump(serializable_segments, f, indent=2)

    def _create_visualizations(self):
        self._create_travel_map()
        self._create_timeline_visualization()
        self._generate_cluster_map_images()
        self._create_travel_pattern_report()

    def _create_travel_map(self):
        geo_df = self.metadata_df.dropna(subset=["Latitude", "Longitude"])
        if len(geo_df) == 0:
            print("No geotagged images for map creation")
            return

        center_lat = geo_df["Latitude"].mean()
        center_lon = geo_df["Longitude"].mean()

        m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

        marker_cluster = MarkerCluster(name="Photos").add_to(m)
        for idx, row in geo_df.iterrows():
            popup_text = f"""
            <b>{row["FileName"]}</b><br>
            Date: {row["DateTime"]}<br>
            Camera: {row.get("CameraModel", "Unknown")}<br>
            """
            folium.Marker(
                [row["Latitude"], row["Longitude"]],
                popup=popup_text,
                icon=folium.Icon(icon="camera", prefix="fa"),
            ).add_to(marker_cluster)

        if self.travel_segments:
            for i, segment in enumerate(self.travel_segments):
                path_coords = [(lat, lon) for lat, lon in segment["points"]]

                if segment["transport_type"] == "Walking":
                    color = "green"
                elif segment["transport_type"] == "Cycling/Urban":
                    color = "blue"
                elif segment["transport_type"] == "Car/Bus":
                    color = "orange"
                elif segment["transport_type"] == "Train":
                    color = "red"
                else:
                    color = "purple"

                popup_text = f"""
                <b>Travel Segment {i + 1}</b><br>
                From: {segment["start_time"].strftime("%Y-%m-%d %H:%M")}<br>
                To: {segment["end_time"].strftime("%Y-%m-%d %H:%M")}<br>
                Distance: {segment["distance_km"]:.2f} km<br>
                Avg Speed: {segment["avg_speed"]:.2f} km/h<br>
                Transport: {segment["transport_type"]}<br>
                """

                folium.PolyLine(
                    path_coords, color=color, weight=3, opacity=0.7, popup=popup_text
                ).add_to(m)

        if self.location_clusters:
            for cluster in self.location_clusters:
                folium.CircleMarker(
                    location=cluster["centroid"],
                    radius=min(20, max(5, cluster["num_photos"])),
                    color="#3186cc",
                    fill=True,
                    fill_color="#3186cc",
                    fill_opacity=0.4,
                    popup=f"""
                    <b>Location Cluster</b><br>
                    Photos: {cluster["num_photos"]}<br>
                    Radius: {cluster["radius_km"]:.2f} km<br>
                    {"Duration: " + str(int(cluster["duration_hours"])) + " hours" if cluster["duration_hours"] else ""}
                    """,
                ).add_to(m)

        folium.LayerControl().add_to(m)

        map_path = os.path.join(self.output_dir, "travel_map.html")
        m.save(map_path)

    def _create_timeline_visualization(self):
        geo_df = self.metadata_df.dropna(subset=["Latitude", "Longitude", "DateTimeObj"])
        if len(geo_df) < 2:
            return

        plt.figure(figsize=(12, 7))

        start_coords = (geo_df["Latitude"].iloc[0], geo_df["Longitude"].iloc[0])
        distances = []

        for _, row in geo_df.iterrows():
            coords = (row["Latitude"], row["Longitude"])
            try:
                dist = geodesic(start_coords, coords).kilometers
            except:
                dist = 0
            distances.append(dist)

        geo_df["distance_from_start"] = distances

        plt.subplot(2, 1, 1)
        plt.plot(geo_df["DateTimeObj"], geo_df["distance_from_start"], "o-", markersize=4)
        plt.title("Distance from Starting Location Over Time")
        plt.ylabel("Distance (km)")
        plt.grid(True, alpha=0.3)

        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        plt.gcf().autofmt_xdate()

        if "CameraModel" in geo_df.columns:
            plt.subplot(2, 1, 2)

            geo_df["date"] = geo_df["DateTimeObj"].dt.date
            camera_counts = geo_df.groupby(["date", "CameraModel"]).size().unstack(fill_value=0)

            camera_counts.plot(kind="bar", stacked=True, ax=plt.gca())
            plt.title("Camera Usage by Date")
            plt.ylabel("Number of Photos")
            plt.xlabel("Date")
            plt.grid(True, alpha=0.3)
            plt.xticks(rotation=45)
            plt.tight_layout()

        timeline_path = os.path.join(self.output_dir, "location_timeline.png")
        plt.tight_layout()
        plt.savefig(timeline_path, dpi=300)
        plt.close()

    def _create_location_cluster_visualization(self):
        if not self.location_clusters or len(self.location_clusters) < 2:
            return

        center_lat = np.mean([c["centroid"][0] for c in self.location_clusters])
        center_lon = np.mean([c["centroid"][1] for c in self.location_clusters])

        m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles="OpenStreetMap")

        for i, cluster in enumerate(self.location_clusters):
            popup_html = f"""
            <div style="width:200px">
                <h4>Location Cluster {i + 1}</h4>
                <p><b>Photos:</b> {cluster["num_photos"]}</p>
                <p><b>Radius:</b> {cluster["radius_km"]:.2f} km</p>
            """

            if cluster["start_time"] and cluster["end_time"]:
                popup_html += f"""
                <p><b>First photo:</b> {cluster["start_time"].strftime("%Y-%m-%d %H:%M")}</p>
                <p><b>Last photo:</b> {cluster["end_time"].strftime("%Y-%m-%d %H:%M")}</p>
                <p><b>Duration:</b> {int(cluster["duration_hours"])} hours</p>
                """

            if cluster["cameras"]:
                popup_html += f"<p><b>Cameras:</b> {', '.join(cluster['cameras'])}</p>"

            popup_html += "</div>"

            folium.Circle(
                location=cluster["centroid"],
                radius=cluster["radius_km"] * 1000,
                color="blue",
                fill=True,
                fill_opacity=0.4,
                popup=folium.Popup(popup_html, max_width=300),
            ).add_to(m)

            folium.Marker(
                location=cluster["centroid"],
                icon=folium.DivIcon(
                    html=f'<div style="font-size: 12pt; color: black; font-weight: bold;">{i + 1}</div>',
                ),
            ).add_to(m)

        cluster_map_path = os.path.join(self.output_dir, "location_clusters.html")
        m.save(cluster_map_path)

    def _detect_anomalies(self, geo_df):
        # AFA port: extracted unchanged from _create_travel_pattern_report() so the
        # anomalies can be reported as findings, not only inside the HTML report.
        anomalies = []

        if self.travel_segments:
            for i, segment in enumerate(self.travel_segments):
                if segment["max_speed"] > 300:
                    anomalies.append(
                        f"Travel segment {i + 1} shows unusually high speed ({segment['max_speed']:.2f} km/h)"
                    )

        if "TimeDelta" in geo_df.columns:
            min_gap_seconds = MIN_SUSPICIOUS_TIME_GAP_DAYS * 24 * 3600
            unusual_gaps = geo_df[geo_df["TimeDelta"] > min_gap_seconds]
            if len(unusual_gaps) > 0:
                for _, row in unusual_gaps.iterrows():
                    gap_days = row["TimeDelta"] / 86400
                    anomalies.append(
                        f"Unusual time gap of {gap_days:.1f} days before photo {row['FileName']}"
                    )

        night_photos = geo_df[
            (geo_df["DateTimeObj"].dt.hour >= 23) | (geo_df["DateTimeObj"].dt.hour <= 5)
        ]
        if len(night_photos) > 0:
            anomalies.append(
                f"Night activity detected: {len(night_photos)} photos taken between 11 PM and 5 AM"
            )

        return anomalies

    def _create_travel_pattern_report(self):
        if not self.output_dir:
            return

        geo_df = self.metadata_df.dropna(subset=["Latitude", "Longitude"])
        if len(geo_df) < 2:
            return

        self._generate_cluster_map_images()

        html_content = """<!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Forensic Travel Pattern Analysis</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 0;
                    padding: 20px;
                    background-color: #f5f5f5;
                    color: #333;
                }}

                h1, h2, h3 {{
                    color: #2c3e50;
                }}

                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                    background-color: white;
                    padding: 20px;
                    box-shadow: 0 0 10px rgba(0,0,0,0.1);
                }}

                .summary-box {{
                    background-color: #f1f8ff;
                    border-left: 4px solid #2980b9;
                    padding: 15px;
                    margin-bottom: 20px;
                }}

                .travel-segment {{
                    background-color: white;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    padding: 15px;
                    margin-bottom: 15px;
                }}

                .travel-segment h3 {{
                    margin-top: 0;
                    border-bottom: 1px solid #eee;
                    padding-bottom: 5px;
                }}

                .travel-segment.flight {{
                    border-left: 4px solid purple;
                }}

                .travel-segment.train {{
                    border-left: 4px solid red;
                }}

                .travel-segment.car {{
                    border-left: 4px solid orange;
                }}

                .travel-segment.cycling {{
                    border-left: 4px solid blue;
                }}

                .travel-segment.walking {{
                    border-left: 4px solid green;
                }}

                .stats-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                    grid-gap: 15px;
                    margin: 20px 0;
                }}

                .stat-card {{
                    background-color: white;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    padding: 15px;
                    text-align: center;
                }}

                .stat-value {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #2980b9;
                }}

                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 20px 0;
                }}

                table, th, td {{
                    border: 1px solid #ddd;
                }}

                th, td {{
                    padding: 12px;
                    text-align: left;
                }}

                th {{
                    background-color: #f2f2f2;
                }}

                .cluster-card {{
                    background-color: white;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    padding: 15px;
                    margin-bottom: 15px;
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    grid-gap: 15px;
                }}

                .cluster-info {{
                    grid-column: 1;
                }}

                .cluster-map {{
                    grid-column: 2;
                    min-height: 300px;
                    background-color: #f5f5f5;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                }}

                .cluster-map iframe {{
                    width: 100%;
                    height: 300px;
                    border: none;
                    border-radius: 4px;
                }}

                img.chart {{
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 0 auto;
                }}

                .image-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                    grid-gap: 10px;
                    margin: 20px 0;
                }}

                .anomaly {{
                    border-left: 4px solid #e74c3c;
                    padding-left: 15px;
                    margin-left: 5px;
                }}

                .link-btn {{
                    display: inline-block;
                    background-color: #3498db;
                    color: white;
                    padding: 8px 15px;
                    text-decoration: none;
                    border-radius: 4px;
                    margin-top: 10px;
                }}

                .link-btn:hover {{
                    background-color: #2980b9;
                }}

                @media (max-width: 768px) {{
                    .cluster-card {{
                        grid-template-columns: 1fr;
                    }}

                    .cluster-map {{
                        grid-column: 1;
                    }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Forensic Travel Pattern Analysis</h1>

                <div class="summary-box">
                    <h2>Analysis Summary</h2>
                    <p>This report contains a detailed analysis of travel patterns extracted from image metadata. It includes information about travel segments, location clusters, and temporal patterns.</p>
                </div>

                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>Total Images</h3>
                        <div class="stat-value">{total_images}</div>
                    </div>
                    <div class="stat-card">
                        <h3>Geotagged Images</h3>
                        <div class="stat-value">{geotagged_images}</div>
                    </div>
                    <div class="stat-card">
                        <h3>Travel Segments</h3>
                        <div class="stat-value">{travel_segments}</div>
                    </div>
                    <div class="stat-card">
                        <h3>Location Clusters</h3>
                        <div class="stat-value">{location_clusters}</div>
                    </div>
                </div>

                <h2>Travel Map</h2>
                <p>Interactive map displaying the complete travel journey with photo locations and detected movement patterns:</p>
                <p>
                    <a href="travel_map.html" class="link-btn">View Travel Map</a>
                </p>

                <h2>Distance & Activity Patterns</h2>
                <img src="location_timeline.png" alt="Location Timeline" class="chart">
                <p>This chart shows the distance from the starting location over time and the distribution of camera usage by date.</p>

                <h2>Travel Pattern Analysis</h2>

                <h3>Travel Segments</h3>
                <p>The following travel segments were identified based on significant location changes:</p>

                {travel_segments_html}

                <h3>Location Clusters</h3>
                <p>The following location clusters represent areas where multiple photos were taken:</p>

                {location_clusters_html}



                <h2>Anomaly Detection</h2>
                <p>The following potential anomalies were detected in the travel patterns:</p>

                {anomalies_html}

                <h2>Conclusions</h2>
                <p>Based on the analysis of image metadata, the following conclusions can be drawn:</p>

                <ul>
                    {conclusions_html}
                </ul>

                <p><em>Report generated on: {generation_date}</em></p>
            </div>

            <script>
                // JavaScript for interactive elements
                document.addEventListener('DOMContentLoaded', function() {{
                    // Add click handlers for expandable sections
                    var expandables = document.querySelectorAll('.expandable-header');
                    expandables.forEach(function(header) {{
                        header.addEventListener('click', function() {{
                            var content = this.nextElementSibling;
                            if (content.style.display === 'none' || !content.style.display) {{
                                content.style.display = 'block';
                                this.classList.add('expanded');
                            }} else {{
                                content.style.display = 'none';
                                this.classList.remove('expanded');
                            }}
                        }});
                    }});
                }});
            </script>
        </body>
        </html>
        """

        total_images = len(self.metadata_df)
        geotagged_images = len(geo_df)
        num_travel_segments = len(self.travel_segments)
        num_location_clusters = len(self.location_clusters)

        travel_segments_html = ""
        if self.travel_segments:
            for i, segment in enumerate(self.travel_segments):
                if segment["transport_type"] == "Walking":
                    segment_class = "walking"
                elif segment["transport_type"] == "Cycling/Urban":
                    segment_class = "cycling"
                elif segment["transport_type"] == "Car/Bus":
                    segment_class = "car"
                elif segment["transport_type"] == "Train":
                    segment_class = "train"
                else:
                    segment_class = "flight"

                travel_segments_html += f"""
                <div class="travel-segment {segment_class}">
                    <h3>Segment {i + 1}: {segment["transport_type"]}</h3>
                    <p><strong>From:</strong> {segment["start_time"].strftime("%Y-%m-%d %H:%M")}</p>
                    <p><strong>To:</strong> {segment["end_time"].strftime("%Y-%m-%d %H:%M")}</p>
                    <p><strong>Duration:</strong> {segment["duration_hours"]:.2f} hours</p>
                    <p><strong>Distance:</strong> {segment["distance_km"]:.2f} km</p>
                    <p><strong>Average Speed:</strong> {segment["avg_speed"]:.2f} km/h</p>
                    <p><strong>Maximum Speed:</strong> {segment["max_speed"]:.2f} km/h</p>
                    <p><strong>Directness Ratio:</strong> {segment["directness_ratio"]:.2f}
                    {"(Direct route)" if segment["directness_ratio"] > 0.8 else "(Indirect route)"}
                    </p>
                    <p><strong>Number of Photos:</strong> {segment["num_photos"]}</p>
                </div>
                """
        else:
            travel_segments_html = "<p>No significant travel segments detected.</p>"

        location_clusters_html = ""
        if self.location_clusters:
            location_clusters_html += "<div class='cluster-list'>"
            for i, cluster in enumerate(self.location_clusters):
                location_clusters_html += f"""
                <div class="cluster-card">
                    <div class="cluster-info">
                        <h3>Cluster {i + 1}</h3>
                        <p><strong>Photos:</strong> {cluster["num_photos"]}</p>
                        <p><strong>Radius:</strong> {cluster["radius_km"]:.2f} km</p>
                """

                if cluster["start_time"] and cluster["end_time"]:
                    location_clusters_html += f"""
                        <p><strong>Period:</strong> {cluster["start_time"].strftime("%Y-%m-%d %H:%M")} to {cluster["end_time"].strftime("%Y-%m-%d %H:%M")}</p>
                        <p><strong>Duration:</strong> {cluster["duration_hours"]:.2f} hours</p>
                    """

                if cluster["cameras"]:
                    location_clusters_html += f"""
                        <p><strong>Cameras:</strong> {", ".join(cluster["cameras"])}</p>
                    """

                location_clusters_html += f"""
                    </div>
                    <div class="cluster-map">
                        <iframe src="cluster_{i + 1}_map.html" title="Cluster {i + 1} Map"></iframe>
                    </div>
                </div>
                """

            location_clusters_html += "</div>"
        else:
            location_clusters_html = "<p>No location clusters detected.</p>"

        time_location_clusters_html = ""
        if self.time_location_clusters:
            time_location_clusters_html += "<div class='cluster-list'>"
            for i, cluster in enumerate(self.time_location_clusters):
                time_location_clusters_html += f"""
                <div class="cluster-card">
                    <div class="cluster-info">
                        <h3>Spatio-Temporal Cluster {i + 1}</h3>
                        <p><strong>Photos:</strong> {cluster["num_photos"]}</p>
                        <p><strong>Spatial Radius:</strong> {cluster["spatial_radius_km"]:.2f} km</p>
                        <p><strong>Period:</strong> {cluster["start_time"].strftime("%Y-%m-%d %H:%M")} to {cluster["end_time"].strftime("%Y-%m-%d %H:%M")}</p>
                        <p><strong>Duration:</strong> {cluster["duration_hours"]:.2f} hours</p>
                """

                if cluster["cameras"]:
                    time_location_clusters_html += f"""
                        <p><strong>Cameras:</strong> {", ".join(cluster["cameras"])}</p>
                    """

                time_location_clusters_html += """
                    </div>
                    <div class="cluster-map">
                        <!-- Placeholder for individual cluster map -->
                        <p>Coordinates: {:.6f}, {:.6f}</p>
                    </div>
                </div>
                """.format(cluster["centroid"][0], cluster["centroid"][1])

            time_location_clusters_html += "</div>"
        else:
            time_location_clusters_html = "<p>No spatio-temporal clusters detected.</p>"

        # AFA port: anomaly rules moved unchanged into _detect_anomalies().
        anomalies = self._detect_anomalies(geo_df)

        if anomalies:
            anomalies_html = "<ul>"
            for anomaly in anomalies:
                anomalies_html += f"<li class='anomaly'>{anomaly}</li>"
            anomalies_html += "</ul>"
        else:
            anomalies_html = "<p>No significant anomalies detected in the travel patterns.</p>"

        conclusions = []

        conclusions.append(
            f"Total of {geotagged_images} geotagged images spanning "
            + f"{(geo_df['DateTimeObj'].max() - geo_df['DateTimeObj'].min()).days} days."
        )

        if self.travel_segments:
            transport_counts = {}
            for segment in self.travel_segments:
                transport_type = segment["transport_type"]
                transport_counts[transport_type] = transport_counts.get(transport_type, 0) + 1

            transport_summary = ", ".join(
                [f"{count} {transport}" for transport, count in transport_counts.items()]
            )
            conclusions.append(f"Travel involved {transport_summary}.")

        if self.location_clusters:
            max_cluster = max(self.location_clusters, key=lambda c: c["num_photos"])
            conclusions.append(
                f"Most significant location had {max_cluster['num_photos']} photos "
                + f"({max_cluster['num_photos'] / geotagged_images * 100:.1f}% of all geotagged images)."
            )

        conclusions_html = ""
        for conclusion in conclusions:
            conclusions_html += f"<li>{conclusion}</li>"

        html_content = html_content.format(
            total_images=total_images,
            geotagged_images=geotagged_images,
            travel_segments=num_travel_segments,
            location_clusters=num_location_clusters,
            travel_segments_html=travel_segments_html,
            location_clusters_html=location_clusters_html,
            time_location_clusters_html=time_location_clusters_html,
            anomalies_html=anomalies_html,
            conclusions_html=conclusions_html,
            generation_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        report_path = os.path.join(self.output_dir, "travel_pattern_report.html")
        with open(report_path, "w") as f:
            f.write(html_content)


def main():
    parser = argparse.ArgumentParser(description="Analyze travel patterns from image metadata")
    parser.add_argument("-m", "--metadata", required=True, help="Path to metadata JSON or CSV file")
    parser.add_argument(
        "-o", "--output", required=True, help="Output directory for analysis results"
    )

    args = parser.parse_args()

    try:
        print(f"Analyzing travel patterns from {args.metadata}...")
        analyzer = GeoTimelineAnalyzer(args.metadata)
        results = analyzer.analyze(args.output)

        if results:
            print(
                f"Analysis complete. Found {len(results['travel_segments'])} travel segments and "
                + f"{len(results['location_clusters'])} location clusters."
            )
            print(f"Results saved to: {args.output}")
        else:
            print("Analysis completed, but no significant patterns were found.")

    except Exception as e:
        import traceback

        print(f"Error: {e!s}")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
