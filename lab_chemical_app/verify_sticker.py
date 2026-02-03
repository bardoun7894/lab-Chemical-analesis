
import os
import sys
from io import BytesIO
from PIL import Image

# Add app directory to path
sys.path.append(os.getcwd())

# Mock classes
class MockProductionOrder:
    def __init__(self):
        self.order_number = "TEST-2026-001"
        self.customer_name = "Test Customer"
        self.sales_number = "SO-12345"
        self.product_code = "P100K9Z1SCB"
        self.product_description = "Test Pipe Description"
        self.product_length = 6

class MockPipe:
    def __init__(self):
        self.id = 1
        self.no_code = "123456"
        self.pipe_code = "P100K9-TEST"
        self.ladle_id = "L-999"
        self.diameter = 100
        self.pipe_class = "K9"
        self.arrange_pipe = 1
        self.production_date = "2026-02-03"
        self.actual_weight = 100.5
        self.production_order = MockProductionOrder()

try:
    from app.routes.stickers import create_sticker_image
    print("Successfully imported create_sticker_image")
except ImportError as e:
    print(f"Import failed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Other error during import: {e}")
    sys.exit(1)

def test_generation():
    pipe = MockPipe()
    decision = "ACCEPT"
    # Medium size in pixels (approx)
    width_px = int(100 * (300 / 25.4))
    height_px = int(60 * (300 / 25.4))
    
    print(f"Generating sticker {width_px}x{height_px}...")
    
    try:
        buffer = create_sticker_image(pipe, decision, width_px, height_px)
        
        output_file = "test_sticker_out.png"
        with open(output_file, "wb") as f:
            f.write(buffer.getvalue())
            
        print(f"Sticker saved to {output_file}")
    except Exception as e:
        print(f"Failed to generate sticker: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_generation()
