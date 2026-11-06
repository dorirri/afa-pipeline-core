"""OCR part of the original prototype's ``ContentAnalyzer``.

Extracted from ``analysis/diplomka/content_analyzer.py`` of the original AFA
prototype: the Tesseract availability check from ``ContentAnalyzer.__init__`` and
``ContentAnalyzer.detect_text_ocr`` unchanged. The rest of that class (YOLO,
InceptionV3, face recognition) loads models and is ported separately. A dead
second copy of ``detect_text_ocr`` that followed the first one (after its
``return``) is not ported. Changes made in the port, each marked ``AFA port``:

* ``lang`` parameter: the original called Tesseract without ``lang`` and therefore
  read English only.
"""

import cv2
import pytesseract


class ContentOcr:
    """Tesseract OCR as done by the prototype's ``ContentAnalyzer``."""

    def __init__(self, lang=None):
        # AFA port: Tesseract language(s), e.g. "kaz+rus+eng"; None = Tesseract default.
        self.lang = lang

        try:
            pytesseract.get_tesseract_version()
            self.ocr_available = True
            print("Tesseract OCR available")
        except Exception as e:
            print(f"Tesseract OCR not available: {e}")
            print("Text detection will be limited")
            self.ocr_available = False

    def detect_text_ocr(self, img):
        """
        Detect and extract text from an image using OCR.

        Args:
            img: OpenCV image (BGR format)

        Returns:
            Dictionary with detected text and coordinates
        """
        if not self.ocr_available:
            return {"text": "", "regions": []}

        try:
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            ocr_data = pytesseract.image_to_data(
                img_rgb,
                output_type=pytesseract.Output.DICT,
                lang=self.lang,  # AFA port
            )

            text_regions = []
            full_text = []

            for i in range(len(ocr_data["text"])):
                text = ocr_data["text"][i].strip()
                conf = int(ocr_data["conf"][i])

                if len(text) > 0 and conf > 30:
                    x = ocr_data["left"][i]
                    y = ocr_data["top"][i]
                    w = ocr_data["width"][i]
                    h = ocr_data["height"][i]

                    text_regions.append(
                        {"text": text, "confidence": conf, "box": [x, y, x + w, y + h]}
                    )

                    full_text.append(text)

            return {"text": " ".join(full_text), "regions": text_regions}

        except Exception as e:
            print(f"Error in OCR: {e!s}")
            return {"text": "", "regions": []}
