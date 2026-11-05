#!/usr/bin/env python3
"""Create realistic Android device images with EXIF metadata for forensic testing.

Ported from the original AFA analysis prototype (``image_generator.py``).
All images are synthetic: coloured canvases with fake devices, times and GPS.

Usage::

    python scripts/image_generator.py --output demo/evidence --count 30 --seed 42
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta

import piexif
from PIL import Image, ImageDraw, ImageFont

COUNTRY_LOCATIONS = {
    "usa": {
        "name": "United States",
        "locations": [
            {"name": "New York City", "coords": (40.7128, -74.0060), "radius": 0.5},
            {"name": "Los Angeles", "coords": (34.0522, -118.2437), "radius": 0.5},
            {"name": "Chicago", "coords": (41.8781, -87.6298), "radius": 0.3},
            {"name": "Miami", "coords": (25.7617, -80.1918), "radius": 0.3},
            {"name": "San Francisco", "coords": (37.7749, -122.4194), "radius": 0.3},
            {"name": "Las Vegas", "coords": (36.1699, -115.1398), "radius": 0.2},
            {"name": "Seattle", "coords": (47.6062, -122.3321), "radius": 0.3},
            {"name": "Boston", "coords": (42.3601, -71.0589), "radius": 0.2},
        ],
    },
    "uk": {
        "name": "United Kingdom",
        "locations": [
            {"name": "London", "coords": (51.5074, -0.1278), "radius": 0.3},
            {"name": "Manchester", "coords": (53.4808, -2.2426), "radius": 0.2},
            {"name": "Birmingham", "coords": (52.4862, -1.8904), "radius": 0.2},
            {"name": "Edinburgh", "coords": (55.9533, -3.1883), "radius": 0.2},
            {"name": "Liverpool", "coords": (53.4084, -2.9916), "radius": 0.2},
            {"name": "Leeds", "coords": (53.8008, -1.5491), "radius": 0.2},
            {"name": "Bristol", "coords": (51.4545, -2.5879), "radius": 0.2},
            {"name": "Glasgow", "coords": (55.8642, -4.2518), "radius": 0.2},
        ],
    },
    "japan": {
        "name": "Japan",
        "locations": [
            {"name": "Tokyo", "coords": (35.6762, 139.6503), "radius": 0.5},
            {"name": "Osaka", "coords": (34.6937, 135.5023), "radius": 0.3},
            {"name": "Kyoto", "coords": (35.0116, 135.7681), "radius": 0.2},
            {"name": "Hiroshima", "coords": (34.3853, 132.4553), "radius": 0.2},
            {"name": "Yokohama", "coords": (35.4437, 139.6380), "radius": 0.3},
            {"name": "Nara", "coords": (34.6851, 135.8048), "radius": 0.1},
            {"name": "Sapporo", "coords": (43.0642, 141.3469), "radius": 0.3},
            {"name": "Fukuoka", "coords": (33.5904, 130.4017), "radius": 0.3},
        ],
    },
}

ANDROID_DEVICES = [
    {
        "make": "Samsung",
        "model": "SM-G991U",
        "software": "Samsung Camera 13.0.00.53",
        "lensModel": "Samsung Galaxy S21 Main Camera",
        "weight": 0.4,
    },
    {
        "make": "Google",
        "model": "Pixel 6",
        "software": "GCamera 8.4.600.453953213.23",
        "lensModel": "Google Pixel 6 Main",
        "weight": 0.15,
    },
    {
        "make": "OnePlus",
        "model": "OnePlus 9",
        "software": "OnePlus Camera 5.3.0.5",
        "lensModel": "OnePlus 9 Main Camera",
        "weight": 0.1,
    },
    {
        "make": "Xiaomi",
        "model": "Mi 11",
        "software": "MIUI Camera 2.5.0",
        "lensModel": "Xiaomi Mi 11 Camera",
        "weight": 0.2,
    },
    {
        "make": "Samsung",
        "model": "SM-A52",
        "software": "Samsung Camera 11.1.1.37",
        "lensModel": "Samsung Galaxy A52 Main",
        "weight": 0.15,
    },
]


def random_coords_near(base_coords, radius_degrees):
    """Generate random coordinates near a base point within a radius."""
    lat, lon = base_coords
    # Add random offset within radius
    lat_offset = random.uniform(-radius_degrees, radius_degrees)
    lon_offset = random.uniform(-radius_degrees, radius_degrees)
    return (lat + lat_offset, lon + lon_offset)


def weighted_random_choice(items):
    """Pick one item using its ``weight`` key."""
    weights = [item["weight"] for item in items]
    return random.choices(items, weights=weights)[0]


def create_metadata_image(device_info, location_info, coords, timestamp, output_path):
    """Draw a labelled canvas, attach device, time and GPS EXIF and save it as JPEG."""
    colors = [
        (64, 128, 192),
        (34, 139, 34),
        (139, 69, 19),
        (128, 128, 128),
        (255, 165, 0),
    ]
    bg_color = random.choice(colors)
    img = Image.new("RGB", (4032, 3024), color=bg_color)

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 100)
        font_small = ImageFont.truetype("arial.ttf", 60)
    except OSError:
        font = ImageFont.load_default()
        font_small = font

    text_lines = [
        f"{device_info['make']} {device_info['model']}",
        f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        f"{location_info['name']}",
        f"Lat: {coords[0]:.4f}, Lon: {coords[1]:.4f}",
    ]

    y_offset = 100
    for i, line in enumerate(text_lines):
        font_to_use = font if i == 0 else font_small
        text_bbox = draw.textbbox((100, y_offset), line, font=font_to_use)
        draw.rectangle(
            [text_bbox[0] - 20, text_bbox[1] - 10, text_bbox[2] + 20, text_bbox[3] + 10],
            fill="black",
        )
        draw.text((100, y_offset), line, fill="white", font=font_to_use)
        y_offset += 80 if i == 0 else 70

    lat, lon = coords

    def dd_to_dms(dd):
        """Convert decimal degrees to degrees, minutes, seconds."""
        is_positive = dd >= 0
        dd = abs(dd)
        degrees = int(dd)
        minutes_float = (dd - degrees) * 60
        minutes = int(minutes_float)
        seconds = (minutes_float - minutes) * 60
        return degrees, minutes, seconds, is_positive

    lat_d, lat_m, lat_s, lat_pos = dd_to_dms(lat)
    lon_d, lon_m, lon_s, lon_pos = dd_to_dms(lon)

    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: device_info["make"],
            piexif.ImageIFD.Model: device_info["model"],
            piexif.ImageIFD.Software: device_info["software"],
            piexif.ImageIFD.DateTime: timestamp.strftime("%Y:%m:%d %H:%M:%S"),
            piexif.ImageIFD.Orientation: 1,
            piexif.ImageIFD.XResolution: (72, 1),
            piexif.ImageIFD.YResolution: (72, 1),
            piexif.ImageIFD.ResolutionUnit: 2,
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: timestamp.strftime("%Y:%m:%d %H:%M:%S"),
            piexif.ExifIFD.DateTimeDigitized: timestamp.strftime("%Y:%m:%d %H:%M:%S"),
            piexif.ExifIFD.ExifVersion: b"0231",
            piexif.ExifIFD.ComponentsConfiguration: b"\x01\x02\x03\x00",
            piexif.ExifIFD.ISOSpeedRatings: random.choice([100, 200, 400, 800]),
            piexif.ExifIFD.ExposureTime: (1, random.randint(30, 500)),
            piexif.ExifIFD.FNumber: (random.choice([140, 170, 200, 280]), 100),
            piexif.ExifIFD.PixelXDimension: 4032,
            piexif.ExifIFD.PixelYDimension: 3024,
            piexif.ExifIFD.ColorSpace: 1,
        },
        "GPS": {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: "N" if lat_pos else "S",
            piexif.GPSIFD.GPSLatitude: ((lat_d, 1), (lat_m, 1), (int(lat_s * 1000000), 1000000)),
            piexif.GPSIFD.GPSLongitudeRef: "E" if lon_pos else "W",
            piexif.GPSIFD.GPSLongitude: ((lon_d, 1), (lon_m, 1), (int(lon_s * 1000000), 1000000)),
            piexif.GPSIFD.GPSAltitudeRef: 0,
            piexif.GPSIFD.GPSAltitude: (random.randint(0, 500), 1),
            piexif.GPSIFD.GPSTimeStamp: (
                (timestamp.hour, 1),
                (timestamp.minute, 1),
                (timestamp.second, 1),
            ),
            piexif.GPSIFD.GPSDateStamp: timestamp.strftime("%Y:%m:%d"),
        },
    }

    if random.random() < 0.3:
        exif_dict["Exif"][piexif.ExifIFD.Flash] = 25
    else:
        exif_dict["Exif"][piexif.ExifIFD.Flash] = 16

    if random.random() < 0.5:
        exif_dict["Exif"][piexif.ExifIFD.FocalLength] = (random.choice([24, 28, 35, 50]), 1)

    # Convert and save
    exif_bytes = piexif.dump(exif_dict)
    img.save(output_path, "JPEG", exif=exif_bytes, quality=90)

    return {
        "filename": os.path.basename(output_path),
        "timestamp": timestamp.isoformat(),
        "location": location_info["name"],
        "coordinates": coords,
        "device": f"{device_info['make']} {device_info['model']}",
    }


def main():
    """Parse arguments and generate the images plus ``generation_summary.json``."""
    parser = argparse.ArgumentParser(
        description="Generate Android device images with realistic metadata"
    )
    parser.add_argument(
        "--output", "-o", required=True, help="Output directory for generated images"
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        choices=["usa", "uk", "japan"],
        default=["usa", "uk"],
        help="Countries to use for locations",
    )
    parser.add_argument("--count", "-c", type=int, default=100, help="Number of images to generate")
    parser.add_argument("--start-date", default="2023-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default="2024-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--clusters", type=int, default=5, help="Number of location/time clusters")
    parser.add_argument("--seed", type=int, help="Random seed for reproducible results")

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    os.makedirs(args.output, exist_ok=True)

    try:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError:
        print("Error: Date format should be YYYY-MM-DD")
        return 1

    all_locations = []
    for country_code in args.countries:
        if country_code in COUNTRY_LOCATIONS:
            all_locations.extend(COUNTRY_LOCATIONS[country_code]["locations"])

    if not all_locations:
        print("Error: No locations found for specified countries")
        return 1

    cluster_info = []
    date_range = (end_date - start_date).days

    for _ in range(args.clusters):
        base_location = random.choice(all_locations)

        cluster_duration = random.randint(1, 7)
        cluster_start = start_date + timedelta(
            days=random.randint(0, max(1, date_range - cluster_duration))
        )

        cluster_info.append(
            {
                "base_location": base_location,
                "start_time": cluster_start,
                "duration_days": cluster_duration,
                "photos_in_cluster": args.count // args.clusters,
            }
        )

    generated_images = []
    image_counter = 0

    for cluster in cluster_info:
        base_loc = cluster["base_location"]
        start_time = cluster["start_time"]

        for _ in range(cluster["photos_in_cluster"]):
            days_offset = random.uniform(0, cluster["duration_days"])
            hours_offset = random.uniform(8, 22)
            minutes_offset = random.uniform(0, 59)

            timestamp = start_time + timedelta(
                days=days_offset, hours=hours_offset, minutes=minutes_offset
            )

            coords = random_coords_near(base_loc["coords"], base_loc["radius"])

            device = weighted_random_choice(ANDROID_DEVICES)

            image_counter += 1
            filename = f"IMG_{image_counter:04d}.jpg"
            output_path = os.path.join(args.output, filename)

            info = create_metadata_image(device, base_loc, coords, timestamp, output_path)
            generated_images.append(info)

            print(f"Created: {filename} ({device['make']} {device['model']} at {base_loc['name']})")

    remaining = args.count - len(generated_images)
    for _ in range(remaining):
        location = random.choice(all_locations)
        timestamp = start_date + timedelta(
            days=random.randint(0, date_range),
            hours=random.uniform(8, 22),
            minutes=random.uniform(0, 59),
        )
        coords = random_coords_near(location["coords"], location["radius"])
        device = weighted_random_choice(ANDROID_DEVICES)

        image_counter += 1
        filename = f"IMG_{image_counter:04d}.jpg"
        output_path = os.path.join(args.output, filename)

        info = create_metadata_image(device, location, coords, timestamp, output_path)
        generated_images.append(info)

        print(f"Created: {filename} ({device['make']} {device['model']} at {location['name']})")

    summary = {
        "total_images": len(generated_images),
        "countries": args.countries,
        "date_range": {"start": args.start_date, "end": args.end_date},
        "clusters": args.clusters,
        "devices_used": list(set(img["device"] for img in generated_images)),
        "locations_used": list(set(img["location"] for img in generated_images)),
        "images": generated_images,
    }

    summary_path = os.path.join(args.output, "generation_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nGenerated {len(generated_images)} images in '{args.output}'")
    print(f"Summary saved to: {summary_path}")
    print("\nCluster distribution:")
    for i, cluster in enumerate(cluster_info):
        print(
            f"  Cluster {i + 1}: {cluster['photos_in_cluster']} photos "
            f"at {cluster['base_location']['name']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
