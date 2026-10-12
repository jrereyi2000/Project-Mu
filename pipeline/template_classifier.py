import cv2
import os
import numpy as np
from typing import Dict, Tuple

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


class TemplateClassifier:
    def __init__(self, template_dir: str = TEMPLATE_DIR, threshold: float = 0.8):
        """
        Initializes the classifier by loading reference template images.
        
        Expected directory structure:
        templates/
            IN_GAMEPLAY.jpg
            BOX_SCORE.jpg
            LEAGUE_STANDINGS.jpg
            HIGHLIGHTS_RECAP.jpg
            
        Note: The templates should be small, static cropped regions (like a header or logo) 
        unique to that specific UI screen, NOT full 1080p screenshots.
        """
        self.threshold = threshold
        self.templates = {}
        
        if os.path.exists(template_dir):
            for filename in os.listdir(template_dir):
                if filename.lower().endswith((".jpg", ".png")):
                    state_name = os.path.splitext(filename)[0]
                    filepath = os.path.join(template_dir, filename)
                    # Load in grayscale for faster, color-invariant matching
                    template_img = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)
                    if template_img is not None:
                        self.templates[state_name] = template_img
                        print(f"[Classifier] Loaded template for state: {state_name}")
                    else:
                        print(f"[Classifier] Failed to load template: {filepath}")
        else:
            print(f"[Classifier] Warning: Template directory '{template_dir}' not found.")
            print("[Classifier] Please create it and add small cropped template images.")

    def detect_ui_state(self, frame) -> Tuple[str, float]:
        """
        Compares the frame against all loaded templates.
        Returns a tuple of (best_matching_state, confidence_score).
        If no template meets the threshold, returns ("UNKNOWN", max_score).
        """
        if not self.templates:
            return "UNKNOWN", 0.0

        # Convert frame to grayscale for template matching
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        best_state = "UNKNOWN"
        best_score = 0.0

        for state_name, template in self.templates.items():
            # Ensure the template is not larger than the frame
            if template.shape[0] > gray_frame.shape[0] or template.shape[1] > gray_frame.shape[1]:
                continue
                
            # Perform template matching using Normalized Correlation Coefficient
            res = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
            
            # minMaxLoc returns the best match position and its score
            _, max_val, _, _ = cv2.minMaxLoc(res)
            
            if max_val > best_score:
                best_score = max_val
                best_state = state_name

        # If the best match beats our threshold, we consider it a positive detection
        if best_score >= self.threshold:
            return best_state, best_score
        else:
            return "UNKNOWN", best_score

# ---------------------------------------------------------
# Test Harness
# ---------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test Phase 2a: Template Classifier")
    parser.add_argument("--image", type=str, required=True, help="Path to full frame to classify (e.g. from test_frames/)")
    parser.add_argument("--templates", type=str, default=TEMPLATE_DIR, help="Directory containing template images")
    parser.add_argument("--threshold", type=float, default=0.9, help="Confidence threshold (0.0 to 1.0)")
    args = parser.parse_args()

    classifier = TemplateClassifier(template_dir=args.templates, threshold=args.threshold)
    
    # Create template directory if it doesn't exist for convenience
    if not os.path.exists(args.templates):
        os.makedirs(args.templates)
        print(f"Created empty directory: {args.templates}/")
        
    frame = cv2.imread(args.image)
    
    if frame is None:
        print(f"Error: Could not load image {args.image}")
    else:
        state, score = classifier.detect_ui_state(frame)
        print(f"\n--- Results ---")
        print(f"Detected State : {state}")
        print(f"Confidence     : {score:.2f} / 1.00")
        if state == "UNKNOWN":
            print("Note: Confidence was below threshold or no templates were found.")
