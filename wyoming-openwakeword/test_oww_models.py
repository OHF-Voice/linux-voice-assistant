
#!/usr/bin/env python3
import os
import time
import argparse
from pathlib import Path

def try_import_tflite():
    try:
        from tflite_runtime.interpreter import Interpreter
        return Interpreter
    except ImportError:
        try:
            from ai_edge_litert.interpreter import Interpreter
            return Interpreter
        except ImportError:
            raise ImportError("Neither tflite_runtime nor ai_edge_litert is installed.")

def test_model(model_path, Interpreter):
    try:
        start = time.time()
        interpreter = Interpreter(model_path=str(model_path))
        interpreter.allocate_tensors()
        elapsed = time.time() - start
        return True, f"OK (loaded in {elapsed:.2f}s)"
    except Exception as e:
        return False, f"FAIL ({str(e)})"

def main():
    parser = argparse.ArgumentParser(description="Test all TFLite models in a directory.")
    parser.add_argument("--models-dir", type=str, default=str(Path.home() / "wyoming-openwakeword" / "wyoming_openwakeword" / "models"),
                        help="Path to the models directory")
    parser.add_argument("--skip-non-detection", action="store_true",
                        help="Skip known non-detection models (embedding_model.tflite, melspectrogram.tflite)")
    parser.add_argument("--quiet", action="store_true", help="Only show failures")
    parser.add_argument("--csv", type=str, help="Output results to CSV file")

    args = parser.parse_args()
    models_dir = Path(args.models_dir)

    Interpreter = try_import_tflite()

    if not models_dir.exists():
        print(f"Models directory not found: {models_dir}")
        return

    skip_list = {"embedding_model.tflite", "melspectrogram.tflite"}
    results = []

    for model_file in sorted(models_dir.glob("*.tflite")):
        if args.skip_non_detection and model_file.name in skip_list:
            if not args.quiet:
                print(f"SKIP {model_file.name}")
            continue

        ok, message = test_model(model_file, Interpreter)
        results.append((model_file.name, ok, message))

        if not args.quiet or not ok:
            status = "PASS" if ok else "FAIL"
            print(f"{status} {model_file.name}: {message}")

    if args.csv:
        import csv
        with open(args.csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Model", "Status", "Message"])
            for name, ok, msg in results:
                writer.writerow([name, "PASS" if ok else "FAIL", msg])
        print(f"Results written to {args.csv}")

if __name__ == "__main__":
    main()
