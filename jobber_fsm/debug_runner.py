#!/usr/bin/env python3
import sys
import os

print(f"[DEBUG] Python version: {sys.version}", flush=True)
print(f"[DEBUG] Python executable: {sys.executable}", flush=True)
print(f"[DEBUG] Current directory: {os.getcwd()}", flush=True)
print(f"[DEBUG] Directory contents: {os.listdir('.')}", flush=True)
print(f"[DEBUG] Environment PATH: {os.environ.get('PATH', 'Not set')}", flush=True)

# Check if playwright is installed
try:
    import playwright
    print(f"[DEBUG] Playwright imported successfully", flush=True)
    # Try to get version from _repo_version.py
    try:
        from playwright._repo_version import version
        print(f"[DEBUG] Playwright version: {version}", flush=True)
    except:
        print(f"[DEBUG] Playwright version: Unable to determine", flush=True)
except ImportError as e:
    print(f"[DEBUG] Playwright import failed: {e}", flush=True)

# Check playwright browsers
import subprocess
try:
    result = subprocess.run(['playwright', '--version'], capture_output=True, text=True)
    print(f"[DEBUG] Playwright CLI version: {result.stdout.strip()}", flush=True)
    
    result = subprocess.run(['playwright', 'show-browsers'], capture_output=True, text=True)
    print(f"[DEBUG] Playwright browsers installed: {result.stdout}", flush=True)
    if result.stderr:
        print(f"[DEBUG] Playwright browsers stderr: {result.stderr}", flush=True)
except Exception as e:
    print(f"[DEBUG] Failed to check playwright browsers: {e}", flush=True)

# Check if chromium exists
try:
    result = subprocess.run(['find', '/ms-playwright', '-name', 'chrome', '-type', 'f'], capture_output=True, text=True)
    print(f"[DEBUG] Chrome binaries found: {result.stdout}", flush=True)
except Exception as e:
    print(f"[DEBUG] Failed to find chrome binaries: {e}", flush=True)

print("[DEBUG] About to import cloud_job_runner...", flush=True)

try:
    from jobber_fsm import cloud_job_runner
    print("[DEBUG] Successfully imported cloud_job_runner", flush=True)
except Exception as e:
    print(f"[DEBUG] Failed to import cloud_job_runner: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("[DEBUG] Starting cloud_job_runner.main()...", flush=True)

# Run the actual job
import asyncio
asyncio.run(cloud_job_runner.main())