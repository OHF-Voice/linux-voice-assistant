#!/usr/bin/env python3
"""
Quick script to reset/turn off all LEDs on the WS2812B strip.
Usage: python3 led_reset.py
"""
import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from state_patterns import all_off, spi

def main():
    all_off()
    time.sleep(0.1)
    spi.close()
    print("LEDs reset: all off and SPI closed.")

if __name__ == "__main__":
    main()
