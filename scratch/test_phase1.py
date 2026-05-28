import time
import logging
import os
from dotenv import load_dotenv
load_dotenv()
from mt5_receiver import TickReceiver

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    print("Initializing TickReceiver...")
    receiver = TickReceiver(symbols=["EURUSD", "GBPUSD"], debug=True)
    
    print("Starting receiver...")
    receiver.start()
    
    print("Running for 30 seconds...")
    time.sleep(30.0)
    
    print("Stopping receiver...")
    receiver.stop()
    
    print("Tick counts collected:")
    print(receiver.tick_counts)
