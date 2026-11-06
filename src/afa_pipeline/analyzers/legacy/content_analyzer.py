"""Content analysis: objects, faces and scene (ported from the original prototype).

Ported from ``analysis/diplomka/content_analyzer.py`` of the original AFA
prototype: ``ContentAnalyzer.__init__``, ``detect_sensitive_content``,
``extract_image_features``, ``analyze_image``, ``_detect_objects_yolo``,
``_detect_faces``, ``_classify_scene`` and ``_detect_text_regions``, with the
forensic categories, thresholds and result format unchanged. Not ported: the
case-level clustering, face-sorted folders (they copy evidence) and HTML reports.
Changes made in the port, each marked ``AFA port``:

* YOLO: ultralytics with local weights (``yolo_weights``) instead of
  ``torch.hub.load("ultralytics/yolov5", "yolov5s")``, which downloads code and
  weights; ``_detect_objects_yolo`` reads the ultralytics result instead of the
  YOLOv5 ``pandas()`` frame.
* InceptionV3: local weights (``inception_weights``) instead of
  ``weights="imagenet"``; TensorFlow is imported only when they are configured.
* OCR lives in ``legacy/content_ocr.py`` and runs as the ``ocr`` analyzer;
  ``detect_text_ocr`` here returns an empty result.
"""

import os
from collections import defaultdict

import cv2
import numpy as np
from PIL import Image


class ContentAnalyzer:
    """Analyze image content using YOLO for object detection and InceptionV3 for sensitive content."""

    def __init__(
        self,
        confidence_threshold=0.45,
        content_detection_threshold=0.7,
        yolo_weights=None,
        inception_weights=None,
    ):
        """Initialize the content analyzer."""
        # AFA port: models are loaded from local files only (yolo_weights,
        # inception_weights); the original downloaded them at start-up.
        self.confidence_threshold = confidence_threshold
        self.content_threshold = content_detection_threshold

        print("Loading YOLO model for object detection...")
        try:
            # AFA port: ultralytics YOLO with local weights instead of
            # torch.hub.load("ultralytics/yolov5", "yolov5s", pretrained=True).
            if not yolo_weights:
                raise FileNotFoundError("no local YOLO weights configured")
            from ultralytics import YOLO

            self.model = YOLO(yolo_weights)
            self.model_loaded = True
            print("YOLO model loaded successfully")
        except Exception as e:
            print(f"Failed to load YOLO model: {e!s}")
            print("Falling back to basic detection methods")
            self.model_loaded = False

        self.forensic_categories = {
            "weapon": ["knife", "scissors", "gun", "rifle", "pistol", "sword"],
            "drug": ["pill", "syringe"],
            "vehicle": ["car", "truck", "motorcycle", "bus", "bicycle"],
            "electronic": [
                "cell phone",
                "laptop",
                "tv",
                "remote",
                "keyboard",
                "mouse",
                "monitor",
            ],
            "document": ["book", "paper"],
            "valuables": ["wallet", "handbag", "backpack", "suitcase", "money"],
            "person": ["person"],
        }

        print("Loading InceptionV3 model for sensitive content detection...")
        try:
            # AFA port: local weights instead of weights="imagenet" (download);
            # TensorFlow is imported only when weights are configured.
            if not inception_weights:
                raise FileNotFoundError("no local InceptionV3 weights configured")
            from tensorflow.keras.applications.inception_v3 import InceptionV3

            self.content_model = InceptionV3(weights=inception_weights)
            self.sensitive_categories = {
                "weapon": ["rifle", "assault_rifle", "revolver", "gun"],
                "violence": ["blood", "dagger", "knife", "sword"],
                "explicit": ["bikini", "brassiere"],
            }
            self.content_model_loaded = True
            print("InceptionV3 model loaded successfully")
        except Exception as e:
            print(f"Failed to load InceptionV3 model: {e!s}")
            self.content_model_loaded = False

        try:
            import face_recognition

            self.face_recognition = face_recognition
            self.face_recognition_loaded = True
            print("Face recognition library loaded successfully")
        except ImportError:
            print("Could not import face_recognition. Face analysis will be skipped.")
            self.face_recognition_loaded = False

        # AFA port: the Tesseract check moved with the OCR code to legacy/content_ocr.py.
        self.ocr_available = False

    def detect_sensitive_content(self, image_path):
        """
        Detect potentially sensitive content in an image
        Returns: dict with detected categories and confidence scores
        """
        try:
            # AFA port: imported here so TensorFlow stays optional.
            from tensorflow.keras.applications.inception_v3 import (
                decode_predictions,
                preprocess_input,
            )

            img = Image.open(image_path).convert("RGB")
            img = img.resize((299, 299))
            x = np.array(img)
            x = np.expand_dims(x, axis=0)
            x = preprocess_input(x)

            preds = self.content_model.predict(x)
            predictions = decode_predictions(preds, top=10)[0]

            detected_content = defaultdict(float)
            for _, label, confidence in predictions:
                for category, keywords in self.sensitive_categories.items():
                    if label in keywords and confidence > self.content_threshold:
                        detected_content[category] = max(detected_content[category], confidence)

            return dict(detected_content)

        except Exception as e:
            print(f"Error analyzing content in {image_path}: {e!s}")
            return {}

    def extract_image_features(self, img):
        """
        Extract features from an image for similarity comparison.
        Uses color histograms and edge histograms for a basic but efficient approach.

        Args:
            img: OpenCV image (BGR format)

        Returns:
            Feature vector as numpy array
        """
        img_resized = cv2.resize(img, (128, 128))

        img_gray = cv2.cvtColor(img_resized, cv2.COLOR_BGR2GRAY)
        img_hsv = cv2.cvtColor(img_resized, cv2.COLOR_BGR2HSV)

        hist_h = cv2.calcHist([img_hsv], [0], None, [32], [0, 180])
        hist_s = cv2.calcHist([img_hsv], [1], None, [32], [0, 256])
        hist_v = cv2.calcHist([img_hsv], [2], None, [32], [0, 256])

        edges = cv2.Canny(img_gray, 100, 200)
        hist_edges = cv2.calcHist([edges], [0], None, [32], [0, 256])

        hist_h = cv2.normalize(hist_h, hist_h).flatten()
        hist_s = cv2.normalize(hist_s, hist_s).flatten()
        hist_v = cv2.normalize(hist_v, hist_v).flatten()
        hist_edges = cv2.normalize(hist_edges, hist_edges).flatten()

        features = np.concatenate([hist_h, hist_s, hist_v, hist_edges])
        return features

    def detect_text_ocr(self, img):
        # AFA port: OCR runs as the separate ``ocr`` analyzer (legacy/content_ocr.py);
        # analyze_image() still calls this method, so it returns an empty result.
        return {"text": "", "regions": []}

    def analyze_image(self, image_path):
        """
        Analyze an image for objects of forensic interest and faces.

        Args:
            image_path: Path to the image file

        Returns:
            Dict with analysis results
        """
        if not os.path.exists(image_path):
            return {"error": f"File not found: {image_path}"}

        try:
            # Load image
            img = cv2.imread(image_path)
            if img is None:
                return {"error": f"Could not read image: {image_path}"}

            results = {
                "filename": os.path.basename(image_path),
                "path": image_path,
                "dimensions": img.shape,
                "objects_detected": [],
                "sensitive_content": {},
                "faces_detected": 0,
                "scene_classification": {},
                "image_features": [],
                "ocr_results": {},
            }

            results["image_features"] = self.extract_image_features(img).tolist()

            if self.model_loaded:
                objects = self._detect_objects_yolo(img)
                results["objects_detected"] = objects

                categories = defaultdict(list)
                for obj in objects:
                    for category, items in self.forensic_categories.items():
                        if obj["class"] in items:
                            categories[category].append(obj)
                            break
                results["forensic_categories"] = dict(categories)

            if self.content_model_loaded:
                sensitive_content = self.detect_sensitive_content(image_path)
                results["sensitive_content"] = sensitive_content

                if sensitive_content and (
                    "weapon" in sensitive_content or "violence" in sensitive_content
                ):
                    if "weapon" not in results["forensic_categories"]:
                        results["forensic_categories"]["weapon"] = []

                    for category, confidence in sensitive_content.items():
                        if category in ["weapon", "violence"]:
                            sensitive_obj = {
                                "class": f"sensitive: {category}",
                                "confidence": confidence,
                                "box": [
                                    10,
                                    10,
                                    img.shape[1] - 10,
                                    img.shape[0] - 10,
                                ],
                            }
                            results["forensic_categories"]["weapon"].append(sensitive_obj)

            if self.face_recognition_loaded:
                faces = self._detect_faces(img)
                results["faces_detected"] = len(faces)
                results["faces"] = faces

            results["ocr_results"] = self.detect_text_ocr(img)

            results["scene_classification"] = self._classify_scene(img)

            text_regions = self._detect_text_regions(img)
            results["potential_text_regions"] = len(text_regions)

            return results

        except Exception as e:
            return {"error": f"Error analyzing image: {e!s}"}

    def _detect_objects_yolo(self, img):
        """Detect objects in an image using YOLO."""
        # AFA port: ultralytics API instead of the YOLOv5 hub ``results.pandas()``.
        # Ultralytics expects OpenCV's BGR order, so ``img`` is passed unconverted;
        # the confidence threshold is passed per call (was ``self.model.conf``).
        results = self.model(img, conf=self.confidence_threshold, verbose=False)[0]

        detections = []

        boxes = results.boxes
        for xyxy, confidence, cls in zip(
            boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), strict=True
        ):
            detection = {
                "class": results.names[int(cls)],
                "confidence": float(confidence),
                "box": [int(v) for v in xyxy],
            }
            detections.append(detection)

        return detections

    def _detect_faces(self, img):
        """Detect faces in an image using face_recognition library."""
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        face_locations = self.face_recognition.face_locations(rgb_img)
        face_encodings = self.face_recognition.face_encodings(rgb_img, face_locations)

        faces = []
        for i, (location, encoding) in enumerate(zip(face_locations, face_encodings)):
            top, right, bottom, left = location
            face_info = {
                "id": i + 1,
                "location": [left, top, right, bottom],
                "encoding": encoding.tolist(),
            }
            faces.append(face_info)

        return faces

    def _classify_scene(self, img):
        """Basic scene classification."""
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        green_lower = np.array([35, 50, 50])
        green_upper = np.array([90, 255, 255])
        green_mask = cv2.inRange(hsv, green_lower, green_upper)
        green_ratio = np.count_nonzero(green_mask) / (img.shape[0] * img.shape[1])

        brightness = np.mean(hsv[:, :, 2])
        is_dark = brightness < 80

        scene_info = {
            "likely_outdoor": green_ratio > 0.15,
            "likely_indoor": green_ratio <= 0.15,
            "likely_night": is_dark,
            "likely_day": not is_dark,
            "brightness_level": int(brightness),
            "color_variety": int(np.std(hsv[:, :, 0])),
        }

        return scene_info

    def _detect_text_regions(self, img):
        """Detect potential text regions in an image."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        edges = cv2.Canny(gray, 100, 200)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        text_regions = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = w / float(h) if h > 0 else 0

            if (
                0.1 < aspect_ratio < 10
                and 10 < w < img.shape[1] // 2
                and 10 < h < img.shape[0] // 3
            ):
                text_regions.append([x, y, w, h])

        return text_regions
