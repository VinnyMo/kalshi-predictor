#!/usr/bin/env python3
"""Test script to debug Kalshi API connection."""

import os
import time
import base64
import requests
from dotenv import load_dotenv
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

load_dotenv()

# Config
API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
PRIVATE_KEY_RAW = os.getenv("KALSHI_PRIVATE_KEY", "").replace("\\n", "\n")
ENVIRONMENT = os.getenv("KALSHI_ENVIRONMENT", "demo")

if ENVIRONMENT == "production":
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
else:
    BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"

print(f"Environment: {ENVIRONMENT}")
print(f"Base URL: {BASE_URL}")
print(f"API Key ID: {API_KEY_ID[:10]}..." if len(API_KEY_ID) > 10 else f"API Key ID: {API_KEY_ID}")
print(f"Private Key loaded: {len(PRIVATE_KEY_RAW)} chars")
print(f"Private Key starts with: {PRIVATE_KEY_RAW[:50]}...")
print()

# Load private key
try:
    private_key = serialization.load_pem_private_key(
        PRIVATE_KEY_RAW.encode("utf-8"),
        password=None,
        backend=default_backend()
    )
    print("✓ Private key loaded successfully")
    print(f"  Key type: {type(private_key).__name__}")
except Exception as e:
    print(f"✗ Failed to load private key: {e}")
    exit(1)

# Generate signature
timestamp = str(int(time.time() * 1000))
method = "GET"
path = "/trade-api/v2/markets"  # Try markets endpoint instead of balance

message = f"{timestamp}{method}{path}".encode("utf-8")

try:
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    signature_b64 = base64.b64encode(signature).decode("utf-8")
    print("✓ Signature generated successfully")
except Exception as e:
    print(f"✗ Failed to generate signature: {e}")
    exit(1)

# Make request
headers = {
    "KALSHI-ACCESS-KEY": API_KEY_ID,
    "KALSHI-ACCESS-TIMESTAMP": timestamp,
    "KALSHI-ACCESS-SIGNATURE": signature_b64,
    "Content-Type": "application/json",
    "Accept": "application/json"
}

url = f"{BASE_URL}/markets?limit=1"
print(f"\nRequesting: {url}")
print(f"Headers: KALSHI-ACCESS-KEY={API_KEY_ID[:10]}..., TIMESTAMP={timestamp}")

try:
    response = requests.get(url, headers=headers, timeout=30)
    print(f"\nResponse Status: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    print(f"\nResponse Body (first 500 chars):")
    print(response.text[:500])

    if response.status_code == 200:
        try:
            data = response.json()
            print(f"\n✓ JSON parsed successfully")
            print(f"  Keys: {list(data.keys())}")
            if "markets" in data:
                print(f"  Markets returned: {len(data['markets'])}")
                if data['markets']:
                    print(f"  First market: {data['markets'][0].get('ticker', 'N/A')}")
        except Exception as e:
            print(f"\n✗ JSON parse error: {e}")

except Exception as e:
    print(f"\n✗ Request failed: {e}")
