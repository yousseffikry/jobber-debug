"""
Screenshot helper for debugging Cloud Run executions
"""
import os
import base64
from datetime import datetime
from typing import Optional
from google.cloud import storage
from playwright.async_api import Page
from jobber_fsm.utils.logger import logger

class ScreenshotDebugger:
    def __init__(self, job_id: str, bucket_name: str = 'jobber-resumes-prod'):
        self.job_id = job_id
        self.bucket_name = bucket_name
        self.screenshot_count = 0
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(bucket_name)
        
    async def capture(self, page: Page, step_name: str, description: str = "") -> Optional[str]:
        """
        Capture a screenshot and upload to GCS
        
        Args:
            page: Playwright page object
            step_name: Name of the current step (e.g., "after_login", "form_filled")
            description: Optional description of what should be visible
            
        Returns:
            GCS path of the uploaded screenshot
        """
        logger.info(f"[ScreenshotDebugger] Attempting to capture screenshot: {step_name}")
        logger.info(f"[ScreenshotDebugger] Job ID: {self.job_id}, Count: {self.screenshot_count}")
        logger.info(f"[ScreenshotDebugger] Page URL: {page.url if page else 'No page'}")
        
        try:
            self.screenshot_count += 1
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            
            logger.info(f"[ScreenshotDebugger] Waiting for page to stabilize...")
            # Wait a bit for page to stabilize
            await page.wait_for_load_state('networkidle', timeout=5000)
            
            logger.info(f"[ScreenshotDebugger] Taking screenshot...")
            # Take screenshot
            screenshot_bytes = await page.screenshot(full_page=True)
            logger.info(f"[ScreenshotDebugger] Screenshot taken, size: {len(screenshot_bytes)} bytes")
            
            # Also capture page info
            page_info = {
                'url': page.url,
                'title': await page.title(),
                'timestamp': timestamp,
                'step': step_name,
                'description': description,
                'viewport': await page.viewport_size(),
            }
            
            # Try to capture any form data (for debugging what was entered)
            try:
                form_data = await page.evaluate('''() => {
                    const inputs = document.querySelectorAll('input, select, textarea');
                    const data = {};
                    inputs.forEach(input => {
                        if (input.name || input.id) {
                            data[input.name || input.id] = {
                                value: input.value,
                                type: input.type,
                                placeholder: input.placeholder,
                                required: input.required
                            };
                        }
                    });
                    return data;
                }''')
                page_info['form_data'] = form_data
            except:
                pass
            
            # File paths
            screenshot_filename = f"{self.screenshot_count:03d}_{step_name}_{timestamp}.png"
            screenshot_path = f"debug/jobs/{self.job_id}/screenshots/{screenshot_filename}"
            
            info_filename = f"{self.screenshot_count:03d}_{step_name}_{timestamp}.json"
            info_path = f"debug/jobs/{self.job_id}/screenshots/{info_filename}"
            
            # Upload screenshot
            screenshot_blob = self.bucket.blob(screenshot_path)
            screenshot_blob.upload_from_string(
                screenshot_bytes,
                content_type='image/png'
            )
            
            # Upload page info
            import json
            info_blob = self.bucket.blob(info_path)
            info_blob.upload_from_string(
                json.dumps(page_info, indent=2),
                content_type='application/json'
            )
            
            # Generate a signed URL for easy viewing (valid for 1 hour)
            signed_url = screenshot_blob.generate_signed_url(
                version="v4",
                expiration=3600,  # 1 hour
                method="GET"
            )
            
            logger.info(f"Screenshot captured: {step_name}")
            logger.info(f"  View at: {signed_url}")
            logger.info(f"  GCS path: gs://{self.bucket_name}/{screenshot_path}")
            
            return f"gs://{self.bucket_name}/{screenshot_path}"
            
        except Exception as e:
            logger.error(f"Failed to capture screenshot for {step_name}: {e}")
            return None
    
    async def capture_with_highlight(self, page: Page, step_name: str, selector: str = None, description: str = "") -> Optional[str]:
        """
        Capture screenshot with optional element highlighting
        
        Args:
            page: Playwright page object
            step_name: Name of the current step
            selector: Optional CSS selector to highlight
            description: Description of what's being highlighted
        """
        try:
            # Highlight element if selector provided
            if selector:
                await page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    if (element) {
                        element.style.border = '3px solid red';
                        element.style.backgroundColor = 'rgba(255, 0, 0, 0.1)';
                        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }''', selector)
                
                # Wait for scroll
                await page.wait_for_timeout(500)
            
            # Take screenshot
            result = await self.capture(page, step_name, description)
            
            # Remove highlight
            if selector:
                await page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    if (element) {
                        element.style.border = '';
                        element.style.backgroundColor = '';
                    }
                }''', selector)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to capture with highlight: {e}")
            return None
    
    async def create_summary(self) -> str:
        """Create a summary page with all screenshots"""
        try:
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            
            # List all screenshots for this job
            prefix = f"debug/jobs/{self.job_id}/screenshots/"
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            
            screenshots = []
            for blob in blobs:
                if blob.name.endswith('.png'):
                    signed_url = blob.generate_signed_url(
                        version="v4",
                        expiration=3600,
                        method="GET"
                    )
                    screenshots.append({
                        'name': blob.name.split('/')[-1],
                        'url': signed_url,
                        'path': f"gs://{self.bucket_name}/{blob.name}"
                    })
            
            # Create HTML summary
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Job Debug: {self.job_id}</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    .screenshot {{ margin: 20px 0; border: 1px solid #ccc; padding: 10px; }}
                    img {{ max-width: 100%; height: auto; }}
                    .info {{ background: #f0f0f0; padding: 10px; margin: 10px 0; }}
                </style>
            </head>
            <body>
                <h1>Debug Screenshots for Job: {self.job_id}</h1>
                <p>Generated at: {timestamp}</p>
                <p>Total screenshots: {len(screenshots)}</p>
                
                <h2>Screenshots:</h2>
            """
            
            for i, screenshot in enumerate(screenshots):
                html += f"""
                <div class="screenshot">
                    <h3>{i+1}. {screenshot['name']}</h3>
                    <p>Path: {screenshot['path']}</p>
                    <img src="{screenshot['url']}" alt="{screenshot['name']}">
                </div>
                """
            
            html += """
            </body>
            </html>
            """
            
            # Upload summary
            summary_path = f"debug/jobs/{self.job_id}/summary.html"
            summary_blob = self.bucket.blob(summary_path)
            summary_blob.upload_from_string(html, content_type='text/html')
            
            # Get signed URL
            summary_url = summary_blob.generate_signed_url(
                version="v4",
                expiration=3600,
                method="GET"
            )
            
            logger.info(f"Debug summary created: {summary_url}")
            return summary_url
            
        except Exception as e:
            logger.error(f"Failed to create summary: {e}")
            return ""
    
    async def test_screenshot(self) -> bool:
        """Test if screenshots are working"""
        logger.info(f"[ScreenshotDebugger] Testing screenshot capability")
        try:
            from jobber_fsm.core.web_driver.playwright import PlaywrightManager
            browser_manager = PlaywrightManager()
            page = await browser_manager.get_current_page()
            
            if not page:
                logger.error("[ScreenshotDebugger] No page available for test")
                return False
                
            # Try to take a test screenshot
            path = await self.capture(page, "test_screenshot", "Testing screenshot capability")
            if path:
                logger.info(f"[ScreenshotDebugger] Test successful: {path}")
                return True
            else:
                logger.error("[ScreenshotDebugger] Test failed")
                return False
        except Exception as e:
            logger.error(f"[ScreenshotDebugger] Test error: {e}", exc_info=True)
            return False