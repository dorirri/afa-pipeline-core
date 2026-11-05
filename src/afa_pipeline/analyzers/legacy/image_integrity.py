"""Error Level Analysis and JPEG ghost analysis (ported from the original prototype).

Ported from ``analysis/diplomka/image_integrity.py`` of the original AFA prototype
with the algorithms unchanged. Changes made in the port, each marked
``AFA port``:

* JPEG ghost writes its temporary re-compressions to ``self.output_dir``
  instead of the current working directory when ``output_dir`` is ``None``.
* JPEG ghost draws its per-quality plots only when ``output_dir`` is given
  (previously every figure was created and never closed).

Use it through :class:`afa_pipeline.analyzers.integrity.IntegrityAnalyzer`, which
runs it in a temporary work directory and converts its output to findings.
"""

import os

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm
from PIL import Image, ImageChops
from scipy.ndimage import label, median_filter


class ImageIntegrityAnalyzer:
    def __init__(self, output_dir="integrity_output"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def perform_ela(self, image_path, quality=85, scale=20):
        try:
            original = Image.open(image_path)
            filename = os.path.basename(image_path)

            if not filename.lower().endswith((".jpg", ".jpeg")):
                return {
                    "status": "error",
                    "message": "ELA analysis requires JPEG image format",
                    "filename": filename,
                }

            resaved_path = os.path.join(self.output_dir, f"temp_{filename}")
            original.save(resaved_path, "JPEG", quality=quality)
            resaved = Image.open(resaved_path)

            ela_image = ImageChops.difference(original, resaved)

            ela_array = np.array(ela_image)

            ela_filtered = median_filter(ela_array, size=3)

            ela_amplified = np.power(ela_filtered.astype(float) / 255.0, 0.5) * 255.0
            ela_amplified = ela_amplified.astype(np.uint8)

            ela_enhanced = Image.fromarray(ela_amplified)

            ela_array_norm = (
                ela_filtered.astype(float) / np.max(ela_filtered)
                if np.max(ela_filtered) > 0
                else ela_filtered
            )
            heatmap = cm.jet(ela_array_norm.mean(axis=2))[:, :, :3]
            heatmap = (heatmap * 255).astype(np.uint8)
            heatmap_image = Image.fromarray(heatmap)

            ela_path = os.path.join(self.output_dir, f"ela_{filename}")
            heatmap_path = os.path.join(self.output_dir, f"ela_heatmap_{filename}")
            ela_enhanced.save(ela_path, "JPEG")
            heatmap_image.save(heatmap_path, "JPEG")

            threshold = np.percentile(ela_filtered.mean(axis=2), 75)
            binary = (ela_filtered.mean(axis=2) > threshold).astype(np.uint8)

            labeled_array, num_features = label(binary)

            region_sizes = []
            for i in range(1, num_features + 1):
                region_size = np.sum(labeled_array == i)
                if region_size > 50:
                    region_sizes.append(region_size)

            total_pixels = ela_array.shape[0] * ela_array.shape[1]

            if len(region_sizes) > 0:
                largest_region = max(region_sizes)
                region_score = largest_region / total_pixels * 100
                manipulation_score = min(region_score * 3, 100)
            else:
                manipulation_score = 0
                largest_region = 0

            std_score = np.std(ela_filtered.mean(axis=2)) * 2

            final_score = max(manipulation_score, std_score)

            os.remove(resaved_path)

            if final_score < 0.5:
                assessment = "No manipulation detected"
                confidence = "High"
            elif final_score < 1.5:
                assessment = "Minor inconsistencies detected, likely normal JPEG artifacts"
                confidence = "Medium"
            elif final_score < 4.0:
                assessment = "Potential manipulation detected"
                confidence = "Medium"
            else:
                assessment = "High probability of manipulation"
                confidence = "High"

            return {
                "status": "success",
                "filename": filename,
                "ela_output_path": ela_path,
                "heatmap_path": heatmap_path,
                "manipulation_score": final_score,
                "assessment": assessment,
                "confidence": confidence,
                "num_suspicious_regions": len(region_sizes),
                "largest_region_size": largest_region if len(region_sizes) > 0 else 0,
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error processing ELA: {e!s}",
                "filename": os.path.basename(image_path),
            }

    def perform_jpeg_ghost_analysis(
        self, image_path, quality_range=[65, 75, 85, 95], output_dir=None
    ):
        try:
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            original = Image.open(image_path)
            filename = os.path.basename(image_path)
            basename = os.path.splitext(filename)[0]

            if not filename.lower().endswith((".jpg", ".jpeg")):
                return {
                    "status": "error",
                    "message": "JPEG Ghost analysis requires JPEG image format",
                    "filename": filename,
                }

            original_array = np.array(original).astype(float)

            ghost_results = []
            max_diff_quality = None
            max_diff_value = 0
            max_diff_map = None

            for quality in quality_range:
                # AFA port: temporary files go to the work dir, never to the CWD.
                temp_path = os.path.join(
                    output_dir if output_dir else self.output_dir,
                    f"temp_ghost_{basename}_{quality}.jpg",
                )
                original.save(temp_path, "JPEG", quality=quality)
                recompressed = Image.open(temp_path)
                recompressed_array = np.array(recompressed).astype(float)

                diff = np.abs(original_array - recompressed_array)

                diff_filtered = median_filter(diff, size=3)

                if len(diff_filtered.shape) == 3:
                    diff_mean = diff_filtered.mean(axis=2)
                else:
                    diff_mean = diff_filtered

                diff_norm = diff_mean / diff_mean.max() if diff_mean.max() > 0 else diff_mean

                # AFA port: plot only when visual output was requested.
                if output_dir:
                    plt.figure(figsize=(10, 8))
                    plt.imshow(diff_norm, cmap="hot")
                    plt.colorbar(label="Difference Intensity")
                    plt.title(f"JPEG Ghost Analysis - Quality {quality}")
                    ghost_path = os.path.join(output_dir, f"ghost_{basename}_q{quality}.jpg")
                    plt.savefig(ghost_path)
                    plt.close()

                avg_diff = np.mean(diff_mean)
                if avg_diff > max_diff_value:
                    max_diff_value = avg_diff
                    max_diff_quality = quality
                    max_diff_map = diff_norm.copy()

                os.remove(temp_path)

                ghost_results.append(
                    {
                        "quality": quality,
                        "average_diff": avg_diff,
                        "max_diff": diff_mean.max(),
                        "ghost_output_path": ghost_path if output_dir else None,
                    }
                )

            threshold = np.percentile(max_diff_map, 90)
            suspicious_mask = (max_diff_map > threshold).astype(np.uint8)

            from scipy.ndimage import label

            labeled_array, num_features = label(suspicious_mask)

            suspicious_regions = []
            for i in range(1, num_features + 1):
                region_size = np.sum(labeled_array == i)
                if region_size > 50:
                    region_indices = np.where(labeled_array == i)
                    min_y, max_y = np.min(region_indices[0]), np.max(region_indices[0])
                    min_x, max_x = np.min(region_indices[1]), np.max(region_indices[1])

                    suspicious_regions.append(
                        {
                            "id": i,
                            "size": region_size,
                            "bounds": [min_x, min_y, max_x, max_y],
                            "center": [np.mean(region_indices[1]), np.mean(region_indices[0])],
                            "intensity": np.mean(max_diff_map[region_indices]),
                        }
                    )

            if output_dir and suspicious_regions:
                plt.figure(figsize=(10, 8))
                plt.imshow(np.array(original))

                for region in suspicious_regions:
                    x_min, y_min, x_max, y_max = region["bounds"]
                    rect = plt.Rectangle(
                        (x_min, y_min),
                        x_max - x_min,
                        y_max - y_min,
                        fill=False,
                        edgecolor="red",
                        linewidth=2,
                    )
                    plt.gca().add_patch(rect)

                    plt.text(
                        region["center"][0],
                        region["center"][1],
                        str(region["id"]),
                        color="white",
                        backgroundcolor="red",
                        fontsize=8,
                    )

                plt.title(f"Suspicious Regions - {len(suspicious_regions)} detected")
                suspect_path = os.path.join(output_dir, f"suspicious_{basename}.jpg")
                plt.savefig(suspect_path)
                plt.close()

            if suspicious_regions:
                largest_region = max(suspicious_regions, key=lambda x: x["size"])
                largest_size = largest_region["size"]
                total_pixels = max_diff_map.shape[0] * max_diff_map.shape[1]
                size_ratio = largest_size / total_pixels

                suspicion_score = size_ratio * 100 * max_diff_value
                suspicion_score = min(suspicion_score * 5, 100)
            else:
                suspicion_score = 0

            if suspicion_score < 10:
                assessment = "No manipulation detected"
                confidence = "High"
            elif suspicion_score < 30:
                assessment = "Minor inconsistencies detected, possibly normal compression artifacts"
                confidence = "Medium"
            elif suspicion_score < 60:
                assessment = "Potential manipulation detected"
                confidence = "Medium"
            else:
                assessment = "High probability of manipulation"
                confidence = "High"

            return {
                "status": "success",
                "filename": filename,
                "ghost_results": ghost_results,
                "best_quality": max_diff_quality,
                "max_difference": max_diff_value,
                "suspicious_regions": suspicious_regions,
                "suspicion_score": suspicion_score,
                "assessment": assessment,
                "confidence": confidence,
                "suspect_visualization": suspect_path
                if output_dir and suspicious_regions
                else None,
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error processing JPEG Ghost Analysis: {e!s}",
                "filename": os.path.basename(image_path) if image_path else "unknown",
            }

    def combine_ela_and_ghost(self, ela_result, ghost_result):
        if ela_result["status"] != "success" or ghost_result["status"] != "success":
            return {
                "status": "error",
                "message": "One or both analyses failed",
                "ela_status": ela_result["status"],
                "ghost_status": ghost_result["status"],
            }

        ela_score = ela_result["manipulation_score"]
        ghost_score = ghost_result["suspicion_score"]

        combined_score = (ela_score + ghost_score) / 2

        if combined_score < 5:
            assessment = "No manipulation detected"
            confidence = "Very High"
        elif combined_score < 20:
            assessment = "Minor inconsistencies detected, likely normal artifacts"
            confidence = "High"
        elif combined_score < 50:
            assessment = "Potential manipulation detected"
            confidence = "Medium"
        else:
            assessment = "High probability of manipulation"
            confidence = "High"

        ela_assessment = ela_result["assessment"]
        ghost_assessment = ghost_result["assessment"]

        if "No manipulation" in ela_assessment and "No manipulation" in ghost_assessment:
            agreement = "Both methods agree: No manipulation detected"
        elif "manipulation" in ela_assessment and "manipulation" in ghost_assessment:
            agreement = "Both methods agree: Manipulation likely"
        else:
            agreement = "Methods show different results - further analysis recommended"
            confidence = "Low"

        ela_regions = ela_result.get("num_suspicious_regions", 0)
        ghost_regions = len(ghost_result.get("suspicious_regions", []))

        return {
            "status": "success",
            "filename": ela_result["filename"],
            "ela_score": ela_score,
            "ghost_score": ghost_score,
            "combined_score": combined_score,
            "assessment": assessment,
            "confidence": confidence,
            "agreement": agreement,
            "ela_regions": ela_regions,
            "ghost_regions": ghost_regions,
            "ela_output": ela_result.get("ela_output_path"),
            "ghost_output": ghost_result.get("suspect_visualization"),
            "detailed_ela": ela_result,
            "detailed_ghost": ghost_result,
        }

    def generate_report(self, ela_results, ghost_results=None, output_path=None):
        if output_path is None:
            output_path = os.path.join(self.output_dir, "enhanced_integrity_report.txt")

        with open(output_path, "w") as f:
            f.write("============================================\n")
            f.write("IMAGE INTEGRITY ANALYSIS REPORT\n")
            f.write("============================================\n\n")

            # Only process ghost results if they were provided
            ghost_dict = {}
            if ghost_results:
                ghost_dict = {r["filename"]: r for r in ghost_results if r["status"] == "success"}

            combined_results = []
            for ela in ela_results:
                if ela["status"] == "success":
                    if ghost_results and ela["filename"] in ghost_dict:
                        ghost = ghost_dict[ela["filename"]]
                        combined = self.combine_ela_and_ghost(ela, ghost)
                        combined_results.append(combined)
                    else:
                        # Only ELA analysis available
                        combined_results.append(
                            {
                                "status": "success",
                                "filename": ela["filename"],
                                "ela_score": ela["manipulation_score"],
                                "ghost_score": None,
                                "combined_score": ela["manipulation_score"],
                                "assessment": ela["assessment"],
                                "confidence": ela["confidence"],
                                "agreement": "Only ELA analysis performed"
                                if not ghost_results
                                else "Ghost analysis not available for this image",
                                "ela_regions": ela.get("num_suspicious_regions", 0),
                                "ghost_regions": 0,
                            }
                        )

            f.write("ANALYSIS SUMMARY\n")
            f.write("--------------------------------------------\n")
            f.write(f"Total images analyzed: {len(ela_results)}\n")
            if ghost_results:
                f.write(
                    f"Images analyzed with both methods: {len([r for r in combined_results if r.get('ghost_score') is not None])}\n"
                )
                f.write(
                    f"Images analyzed with ELA only: {len([r for r in combined_results if r.get('ghost_score') is None])}\n"
                )
            else:
                f.write("Analysis method: ELA only\n")

            suspicious_count = sum(1 for r in combined_results if r["combined_score"] > 20)
            f.write(f"Images with potential manipulation: {suspicious_count}\n\n")

            sorted_results = sorted(
                combined_results, key=lambda x: x["combined_score"], reverse=True
            )

            if suspicious_count > 0:
                f.write("SUSPICIOUS IMAGES (Highest manipulation scores):\n")
                for result in sorted_results[: min(5, suspicious_count)]:
                    f.write(f"- {result['filename']}:\n")
                    if result.get("ghost_score") is not None:
                        f.write(f"  Combined Score: {result['combined_score']:.2f}%\n")
                        f.write(
                            f"  ELA Score: {result['ela_score']:.2f}% / Ghost Score: {result['ghost_score']:.2f}%\n"
                        )
                        f.write(
                            f"  Suspicious Regions: {result['ela_regions']} (ELA), {result['ghost_regions']} (Ghost)\n"
                        )
                    else:
                        f.write(f"  ELA Score: {result['ela_score']:.2f}%\n")
                        f.write(f"  Suspicious Regions: {result['ela_regions']} (ELA)\n")

                    f.write(
                        f"  Assessment: {result['assessment']} (Confidence: {result['confidence']})\n"
                    )
                    f.write(f"  Analysis Status: {result['agreement']}\n")

                    if result.get("ela_output"):
                        f.write(f"  ELA output: {os.path.basename(result['ela_output'])}\n")
                    if result.get("ghost_output"):
                        f.write(
                            f"  Ghost visualization: {os.path.basename(result['ghost_output'])}\n"
                        )
                    f.write("\n")

            f.write("OVERALL ASSESSMENT\n")
            f.write("--------------------------------------------\n")

            high_confidence_issues = sum(
                1 for r in sorted_results if r["combined_score"] > 50 and r["confidence"] == "High"
            )

            if high_confidence_issues > 0:
                f.write("ALERT: Significant integrity issues detected with high confidence.\n")
                f.write("Further forensic analysis recommended.\n")
            elif suspicious_count > 0:
                f.write("CAUTION: Some potential integrity issues detected.\n")
                f.write("Review flagged items for further investigation.\n")
            else:
                f.write("PASSED: No significant integrity issues detected.\n")

            if not ghost_results:
                f.write("\nNOTE: Analysis performed using ELA only. ")
                f.write("For more comprehensive analysis, consider running with --ghost flag.\n")

            f.write("\n============================================\n")

        return output_path

    def analyze_directory(
        self, directory_path, output_dir="integrity_output", enable_ghost=False, debug_mode=False
    ):
        analyzer = self

        ela_results = []
        ghost_results = [] if enable_ghost else None
        image_extensions = [".jpg", ".jpeg"]

        print("Starting image integrity analysis...")
        print("ELA Analysis: Enabled")
        print(f"Ghost Analysis: {'Enabled' if enable_ghost else 'Disabled'}")
        print(f"Debug Mode: {'Enabled' if debug_mode else 'Disabled'}")
        print("-" * 50)

        for root, _, files in os.walk(directory_path):
            for file in files:
                if any(file.lower().endswith(ext) for ext in image_extensions):
                    file_path = os.path.join(root, file)

                    if debug_mode:
                        print(f"Processing: {file_path}")

                    ela_result = analyzer.perform_ela(file_path)
                    ela_results.append(ela_result)

                    if enable_ghost:
                        ghost_result = analyzer.perform_jpeg_ghost_analysis(
                            file_path, quality_range=[65, 75, 85, 95], output_dir=output_dir
                        )
                        ghost_results.append(ghost_result)

                    if debug_mode and ela_result["status"] == "success":
                        print(f"  ELA manipulation score: {ela_result['manipulation_score']:.2f}")
                        print(f"  ELA assessment: {ela_result['assessment']}")

                        if enable_ghost and ghost_result["status"] == "success":
                            print(f"  Ghost suspicion score: {ghost_result['suspicion_score']:.2f}")
                            print(f"  Ghost assessment: {ghost_result['assessment']}")
                            print(
                                f"  Suspicious regions: {len(ghost_result.get('suspicious_regions', []))}"
                            )

                            combined = analyzer.combine_ela_and_ghost(ela_result, ghost_result)
                            print(f"  COMBINED score: {combined['combined_score']:.2f}")
                            print(
                                f"  COMBINED assessment: {combined['assessment']} ({combined['confidence']})"
                            )
                            print(f"  Agreement: {combined['agreement']}")
                        elif enable_ghost:
                            print(
                                f"  Ghost analysis failed: {ghost_result.get('message', 'Unknown error')}"
                            )

        report_path = analyzer.generate_report(ela_results, ghost_results)

        analysis_type = "ELA + Ghost" if enable_ghost else "ELA only"
        print(f"{analysis_type} analysis complete. Report saved to: {report_path}")
        return report_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze image integrity using Error Level Analysis and optionally JPEG Ghost Analysis"
    )
    parser.add_argument("--dir", required=True, help="Directory containing images to analyze")
    parser.add_argument("--output", default="integrity_output", help="Output directory")
    parser.add_argument(
        "--ghost",
        action="store_true",
        help="Enable JPEG Ghost Analysis (optional, adds processing time)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode for verbose output")

    args = parser.parse_args()

    analyzer = ImageIntegrityAnalyzer(output_dir=args.output)
    report_path = analyzer.analyze_directory(
        args.dir, args.output, enable_ghost=args.ghost, debug_mode=args.debug
    )
