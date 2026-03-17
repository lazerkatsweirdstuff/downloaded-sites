# save as unified_offline_browser_fixed.py
import socket
import threading
import subprocess
import sys
import os
import ctypes
import time
import select
import ssl
import zipfile
import json
import webbrowser
from urllib.parse import urlparse, unquote, urljoin
import base64
import hashlib
import re
from bs4 import BeautifulSoup
import html
import tempfile
import shutil
import signal
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configuration
PROXY_PORT = 8080
BROWSER_PORT = 8000
YOUR_IP = "192.168.137.1"

# HTML content for intercepted sites
LOL_HTML_CONTENT = b"""<!DOCTYPE html>
<html>
<head><title>lol</title></head>
<body style="display: flex; justify-content: center; align-items: center; height: 100vh; font-size: 100px; margin: 0; font-family: Arial, sans-serif;">
lol
</body>
</html>"""

# Self-signed certificate (for development only)
CERT_FILE = "lol_cert.pem"
KEY_FILE = "lol_key.pem"

def get_script_directory():
    """Get the directory where the script is located"""
    return os.path.dirname(os.path.abspath(__file__))

def create_self_signed_cert():
    """Create a self-signed certificate for SSL interception"""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return
    
    try:
        from OpenSSL import crypto
    except ImportError:
        print("\n📦 Installing required package: pyOpenSSL")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyopenssl"])
        from OpenSSL import crypto
    
    # Generate key
    key = crypto.PKey()
    key.generate_key(crypto.TYPE_RSA, 2048)
    
    # Generate certificate
    cert = crypto.X509()
    cert.get_subject().CN = "*.example.com"
    cert.set_serial_number(1000)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(10*365*24*60*60)  # 10 years
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(key)
    cert.sign(key, 'sha256')
    
    # Save certificate
    with open(CERT_FILE, "wb") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
    
    # Save key
    with open(KEY_FILE, "wb") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, key))
    
    print("✅ Created self-signed certificate")

class OfflinePageBrowser:
    """Manages offline .page files and serves them"""
    
    def __init__(self, pages_directory=None):
        script_dir = get_script_directory()
        if pages_directory is None:
            self.pages_directory = os.path.join(script_dir, "downloaded_sites")
        else:
            self.pages_directory = os.path.abspath(pages_directory)
            
        print(f"📁 Script location: {script_dir}")
        print(f"📁 Browser looking for .page files in: {self.pages_directory}")
        self.loaded_sites = {}
        self.youtube_videos = []
        
        # Create temp directory for extracted videos
        self.temp_dir = tempfile.mkdtemp(prefix="youtube_browser_")
        print(f"📁 Temp directory for videos: {self.temp_dir}")
        
        # Check if directory exists
        if not os.path.exists(self.pages_directory):
            print(f"⚠️ Directory not found: {self.pages_directory}")
            print("Please run the downloader first or specify the correct path.")
    
    def __del__(self):
        """Clean up temp directory when browser is destroyed"""
        if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                print(f"🧹 Cleaned up temp directory: {self.temp_dir}")
            except Exception as e:
                print(f"⚠️ Could not clean up temp directory: {e}")
    
    def extract_video_from_page(self, page_path, video_id):
        """Extract video from .page file to temp directory"""
        try:
            video_temp_path = os.path.join(self.temp_dir, f"{video_id}.mp4")
            
            if os.path.exists(video_temp_path):
                return video_temp_path
            
            with zipfile.ZipFile(page_path, 'r') as zipf:
                for file_info in zipf.filelist:
                    if file_info.filename == 'video.mp4' or file_info.filename.endswith('.mp4'):
                        with zipf.open(file_info.filename) as video_file:
                            with open(video_temp_path, 'wb') as f:
                                f.write(video_file.read())
                        print(f"🎬 Extracted video to temp: {video_temp_path}")
                        return video_temp_path
                
                print(f"❌ No video found in .page file: {page_path}")
                return None
            
        except Exception as e:
            print(f"❌ Error extracting video from {page_path}: {e}")
            return None
    
    def load_page_file(self, filepath):
        """Load a .page file into memory"""
        try:
            with zipfile.ZipFile(filepath, 'r') as zipf:
                if 'metadata.json' not in zipf.namelist():
                    return None
                    
                metadata_str = zipf.read('metadata.json').decode('utf-8')
                metadata = json.loads(metadata_str)
                
                if metadata.get('type') == 'youtube_video':
                    video_id = metadata.get('video_id', 'unknown')
                    video_title = metadata.get('title', 'Unknown Title')
                    
                    video_temp_path = self.extract_video_from_page(filepath, video_id)
                    if not video_temp_path:
                        print(f"⚠️ Could not extract video: {video_id}")
                        return None
                    
                    if 'index.html' in zipf.namelist():
                        html_content = zipf.read('index.html').decode('utf-8')
                        html_content = html_content.replace('src="video.mp4"', f'src="/temp_videos/{video_id}.mp4"')
                        
                        page_data = {
                            'url': metadata.get('original_url', f"youtube_video_{video_id}"),
                            'content': html_content,
                            'content_type': 'text/html',
                            'status_code': 200,
                            'downloaded_with': 'youtube_downloader',
                            'video_id': video_id,
                            'temp_video_path': video_temp_path,
                            'page_file': filepath
                        }
                        
                        domain = f"youtube_{video_id}"
                        site_data = {
                            'metadata': metadata,
                            'pages': {domain: page_data},
                            'assets': {},
                            'is_youtube': True,
                            'video_temp_path': video_temp_path
                        }
                        
                        self.loaded_sites[domain] = site_data
                        self.youtube_videos.append({
                            'video_id': video_id,
                            'title': video_title,
                            'channel': metadata.get('channel', 'Unknown Channel'),
                            'domain': domain,
                            'filepath': filepath,
                            'temp_video_path': video_temp_path
                        })
                        
                        print(f"✅ Loaded YouTube video: {video_title}")
                        return site_data
                else:
                    pages = {}
                    assets = {}
                    
                    for file_info in zipf.filelist:
                        if file_info.filename.startswith('pages/') and file_info.filename.endswith('.json'):
                            page_data_str = zipf.read(file_info.filename).decode('utf-8')
                            page_data = json.loads(page_data_str)
                            pages[page_data['url']] = page_data
                        
                        elif file_info.filename.startswith('assets/') and file_info.filename.endswith('.json'):
                            asset_data_str = zipf.read(file_info.filename).decode('utf-8')
                            asset_data = json.loads(asset_data_str)
                            assets[asset_data['url']] = asset_data
                    
                    site_data = {
                        'metadata': metadata,
                        'pages': pages,
                        'assets': assets,
                        'is_youtube': False
                    }
                    
                    domain = metadata.get('main_url', 'unknown_site')
                    self.loaded_sites[domain] = site_data
                    print(f"✅ Loaded site: {domain} with {len(pages)} pages")
                    return site_data
                
        except Exception as e:
            print(f"❌ Error loading {filepath}: {e}")
            return None
    
    def load_all_page_files(self):
        """Load all .page files from the directory"""
        if not os.path.exists(self.pages_directory):
            print(f"❌ Directory {self.pages_directory} does not exist")
            return
        
        page_files = []
        for root, dirs, files in os.walk(self.pages_directory):
            for file in files:
                if file.endswith('.page'):
                    page_files.append(os.path.join(root, file))
        
        print(f"📄 Found {len(page_files)} .page files:")
        for filepath in page_files:
            relative_path = os.path.relpath(filepath, self.pages_directory)
            print(f"  • Loading: {relative_path}")
            self.load_page_file(filepath)
        
        regular_sites = sum(1 for s in self.loaded_sites.values() if not s.get('is_youtube', False))
        youtube_videos = len(self.youtube_videos)
        
        print(f"✅ Total sites loaded: {regular_sites} regular sites")
        print(f"✅ YouTube videos loaded: {youtube_videos}")
    
    def find_page_by_url(self, url):
        """Find a page across all loaded sites by URL"""
        if url.startswith('youtube_'):
            for domain, site_data in self.loaded_sites.items():
                if domain == url:
                    return next(iter(site_data['pages'].values()))
        
        for site_data in self.loaded_sites.values():
            if url in site_data['pages']:
                return site_data['pages'][url]

        if url.startswith('http://'):
            alt_url = url.replace('http://', 'https://', 1)
            for site_data in self.loaded_sites.values():
                if alt_url in site_data['pages']:
                    return site_data['pages'][alt_url]
        elif url.startswith('https://'):
            alt_url = url.replace('https://', 'http://', 1)
            for site_data in self.loaded_sites.values():
                if alt_url in site_data['pages']:
                    return site_data['pages'][alt_url]

        for site_data in self.loaded_sites.values():
            for page_url, page_data in site_data['pages'].items():
                parsed_request = urlparse(url)
                parsed_page = urlparse(page_url)

                if parsed_request.path and parsed_request.path == parsed_page.path:
                    return page_data

                if (parsed_request.netloc == parsed_page.netloc and 
                    parsed_request.path in parsed_page.path):
                    return page_data

        return None

    def find_asset_by_url(self, url):
        """Find an asset across all loaded sites by URL"""
        for site_data in self.loaded_sites.values():
            if url in site_data['assets']:
                return site_data['assets'][url]

        if url.startswith('http://'):
            alt_url = url.replace('http://', 'https://', 1)
            for site_data in self.loaded_sites.values():
                if alt_url in site_data['assets']:
                    return site_data['assets'][alt_url]
        elif url.startswith('https://'):
            alt_url = url.replace('https://', 'http://', 1)
            for site_data in self.loaded_sites.values():
                if alt_url in site_data['assets']:
                    return site_data['assets'][alt_url]

        requested_filename = os.path.basename(urlparse(url).path)
        if requested_filename:
            for site_data in self.loaded_sites.values():
                for asset_url, asset_data in site_data['assets'].items():
                    asset_filename = os.path.basename(urlparse(asset_url).path)
                    if asset_filename == requested_filename:
                        return asset_data
        
        return None
    
    def find_asset_by_relative_path(self, path):
        """Find an asset by relative path"""
        if path.startswith('/'):
            path = path[1:]
        
        for site_data in self.loaded_sites.values():
            for asset_url, asset_data in site_data['assets'].items():
                parsed = urlparse(asset_url)
                asset_path = parsed.path
                if asset_path.startswith('/'):
                    asset_path = asset_path[1:]
                
                if asset_url.endswith(path) or asset_path == path:
                    return asset_data
        
        return None

class UnifiedProxyAndBrowser:
    """Unified server that handles both proxy interception and offline browsing"""
    
    def __init__(self, proxy_port=8080, browser_port=8000):
        self.proxy_port = proxy_port
        self.browser_port = browser_port
        self.proxy_running = True
        self.browser_running = True
        self.local_ip = self.get_local_ip()
        
        # Initialize offline browser
        self.offline_browser = OfflinePageBrowser()
        self.offline_browser.load_all_page_files()
    
    def get_local_ip(self):
        """Get local IP address"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "localhost"
    
    def get_not_found_page(self, requested_url):
        """Generate a friendly not-found page"""
        regular_sites = sum(1 for s in self.offline_browser.loaded_sites.values() if not s.get('is_youtube', False))
        youtube_videos = len(self.offline_browser.youtube_videos)
        
        # Get some example sites
        examples = []
        for domain, site_data in list(self.offline_browser.loaded_sites.items())[:3]:
            if not site_data.get('is_youtube', False):
                examples.append(domain)
        
        # Suggest YouTube if available
        youtube_suggestion = ""
        if youtube_videos > 0:
            youtube_suggestion = f"""
            <div class="youtube-section">
                <p>🎬 Or check out our {youtube_videos} YouTube videos:</p>
                <div style="margin-top: 10px;">
                    <a href="http://{self.local_ip}:{self.browser_port}/youtube" class="btn" style="background: #ff0000;">🎬 Watch YouTube Videos</a>
                </div>
            </div>
            """
        
        examples_text = ", ".join(examples[:3]) if examples else "example.com (try intercepting this one!)"
        
        # HTML template
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Page Not Found</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    width: 100%;
                }}
                .card {{
                    background: rgba(255, 255, 255, 0.95);
                    backdrop-filter: blur(10px);
                    padding: 40px;
                    border-radius: 20px;
                    box-shadow: 0 20px 40px rgba(0,0,0,0.2);
                    text-align: center;
                }}
                h1 {{
                    font-size: 3em;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    margin-bottom: 20px;
                }}
                .emoji {{
                    font-size: 5em;
                    margin-bottom: 20px;
                }}
                .url {{
                    background: #f3f4f6;
                    padding: 10px;
                    border-radius: 10px;
                    margin: 20px 0;
                    word-break: break-all;
                    color: #666;
                }}
                .btn {{
                    display: inline-block;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    color: white;
                    text-decoration: none;
                    padding: 15px 30px;
                    border-radius: 30px;
                    font-weight: bold;
                    margin-top: 20px;
                    transition: transform 0.3s ease;
                }}
                .btn:hover {{
                    transform: translateY(-2px);
                }}
                .stats {{
                    margin: 20px 0;
                    padding: 20px;
                    background: linear-gradient(135deg, #667eea20, #764ba220);
                    border-radius: 10px;
                }}
                .stat-grid {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 15px;
                    margin-top: 15px;
                }}
                .stat-item {{
                    background: white;
                    padding: 15px;
                    border-radius: 10px;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                }}
                .stat-number {{
                    font-size: 2em;
                    font-weight: bold;
                    color: #667eea;
                }}
                .stat-label {{
                    color: #666;
                    font-size: 0.9em;
                }}
                .youtube-section {{
                    margin-top: 20px;
                    border-top: 1px solid #eaeaea;
                    padding-top: 20px;
                }}
                .info-box {{
                    background: #e3f2fd;
                    border-left: 4px solid #2196f3;
                    padding: 15px;
                    margin-top: 20px;
                    border-radius: 5px;
                    text-align: left;
                }}
                .info-box code {{
                    background: #f0f0f0;
                    padding: 2px 5px;
                    border-radius: 3px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="card">
                    <div class="emoji">🔍</div>
                    <h1>Page Not Downloaded</h1>
                    <p>Sorry, no page has been downloaded for:</p>
                    <div class="url">{requested_url}</div>
                    
                    <div class="stats">
                        <p><strong>But we have these available:</strong></p>
                        <div class="stat-grid">
                            <div class="stat-item">
                                <div class="stat-number">{regular_sites}</div>
                                <div class="stat-label">Regular Sites</div>
                            </div>
                            <div class="stat-item">
                                <div class="stat-number">{youtube_videos}</div>
                                <div class="stat-label">YouTube Videos</div>
                            </div>
                        </div>
                    </div>
                    
                    {youtube_suggestion}
                    
                    <a href="http://{self.local_ip}:{self.browser_port}/" class="btn">📚 Browse All Available Pages</a>
                    
                    <div class="info-box">
                        <strong>💡 Proxy Info:</strong> Your proxy is set to <code>{self.local_ip}:{self.proxy_port}</code><br>
                        <small>Try visiting one of our downloaded sites, or example.com for a fun intercept!</small>
                    </div>
                    
                    <p style="margin-top: 20px; color: #999; font-size: 0.9em;">
                        Examples: {examples_text}
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def start_proxy(self):
        """Start the proxy server in a separate thread"""
        def proxy_thread():
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            try:
                server.bind(('0.0.0.0', self.proxy_port))
                server.listen(100)
                print(f"🔄 Proxy listening on port {self.proxy_port}")
                
                while self.proxy_running:
                    try:
                        client, addr = server.accept()
                        threading.Thread(target=self.handle_proxy_client, args=(client, addr), daemon=True).start()
                    except:
                        break
            except Exception as e:
                print(f"❌ Proxy error: {e}")
            finally:
                server.close()
        
        thread = threading.Thread(target=proxy_thread, daemon=True)
        thread.start()
        return thread
    
    def handle_proxy_client(self, client_socket, addr):
        """Handle individual proxy client connection"""
        try:
            client_socket.settimeout(10)
            
            request = client_socket.recv(8192)
            if not request:
                return
            
            first_line = request.split(b'\n')[0].decode('utf-8', errors='ignore')
            print(f"📨 {first_line[:50]}... from {addr[0]}")
            
            if first_line.startswith('CONNECT'):
                self.handle_proxy_connect(request, client_socket, addr)
            else:
                self.handle_proxy_http(request, client_socket, addr)
                
        except Exception as e:
            print(f"❌ Client error: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
    
    def handle_proxy_connect(self, request, client_socket, addr):
        """Handle HTTPS CONNECT method"""
        try:
            parts = request.split(b' ')
            if len(parts) < 2:
                return
            
            host_port = parts[1].decode('utf-8', errors='ignore')
            host = host_port.split(':')[0]
            
            # Check for example.com (intercept)
            if 'example.com' in host:
                print(f"🎯 Intercepted HTTPS for {host}")
                client_socket.send(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(CERT_FILE, KEY_FILE)
                
                ssl_client = context.wrap_socket(client_socket, server_side=True)
                
                http_response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html\r\n"
                    f"Content-Length: {len(LOL_HTML_CONTENT)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode('utf-8') + LOL_HTML_CONTENT
                
                try:
                    ssl_client.send(http_response)
                except Exception as e:
                    print(f"❌ SSL send error: {e}")
                finally:
                    ssl_client.close()
                
                return
            
            # For all other HTTPS requests, check offline browser
            if self.handle_offline_request(host, client_socket, is_https=True):
                return
            
            # If not found, forward
            self.forward_proxy_connect(request, client_socket, host_port)
            
        except Exception as e:
            print(f"❌ CONNECT error: {e}")
    
    def handle_proxy_http(self, request, client_socket, addr):
        """Handle HTTP requests"""
        try:
            request_str = request.decode('utf-8', errors='ignore')
            
            # Extract host from request
            host = self.extract_host(request_str)
            
            # Check for example.com (intercept)
            if 'example.com' in request_str or (host and 'example.com' in host):
                print(f"🎯 Intercepted HTTP for example.com")
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html\r\n"
                    f"Content-Length: {len(LOL_HTML_CONTENT)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode('utf-8') + LOL_HTML_CONTENT
                
                client_socket.send(response)
                return
            
            # For all other HTTP requests, check offline browser
            if host and self.handle_offline_request(host, client_socket, is_https=False, request=request):
                return
            
            # If not found, serve not-found page
            self.serve_not_found_page(client_socket, host or "unknown", is_https=False)
            
        except Exception as e:
            print(f"❌ HTTP error: {e}")
    
    def handle_offline_request(self, host, client_socket, is_https=False, request=None):
        """Check if host is in offline browser and serve it"""
        
        # Check if host is a YouTube video
        for video in self.offline_browser.youtube_videos:
            if host in video['domain'] or video['title'] in host:
                print(f"🎬 Serving offline YouTube video for: {host}")
                
                # Get the site data
                site_data = self.offline_browser.loaded_sites.get(video['domain'])
                if site_data:
                    page_data = next(iter(site_data['pages'].values()))
                    content = page_data['content']
                    
                    if is_https:
                        # Handle HTTPS tunnel
                        try:
                            client_socket.send(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            
                            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                            context.load_cert_chain(CERT_FILE, KEY_FILE)
                            
                            ssl_client = context.wrap_socket(client_socket, server_side=True)
                            
                            response = (
                                "HTTP/1.1 200 OK\r\n"
                                "Content-Type: text/html\r\n"
                                f"Content-Length: {len(content)}\r\n"
                                "Connection: close\r\n"
                                "\r\n"
                            ).encode('utf-8') + content.encode('utf-8')
                            
                            ssl_client.send(response)
                            ssl_client.close()
                        except:
                            pass
                    else:
                        # Handle HTTP
                        response = (
                            "HTTP/1.1 200 OK\r\n"
                            "Content-Type: text/html\r\n"
                            f"Content-Length: {len(content)}\r\n"
                            "Connection: close\r\n"
                            "\r\n"
                        ).encode('utf-8') + content.encode('utf-8')
                        
                        client_socket.send(response)
                    
                    return True
        
        # Check if host is a regular downloaded site
        for domain, site_data in self.offline_browser.loaded_sites.items():
            if not site_data.get('is_youtube', False) and (host in domain or domain in host):
                print(f"📚 Serving offline page for: {host}")
                
                # Get first page from this site
                if site_data['pages']:
                    page_data = next(iter(site_data['pages'].values()))
                    content = page_data['content']
                    
                    # Rewrite links to work with proxy
                    content = self.rewrite_links_for_proxy(content, page_data['url'])
                    
                    if is_https:
                        try:
                            client_socket.send(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            
                            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                            context.load_cert_chain(CERT_FILE, KEY_FILE)
                            
                            ssl_client = context.wrap_socket(client_socket, server_side=True)
                            
                            response = (
                                "HTTP/1.1 200 OK\r\n"
                                "Content-Type: text/html\r\n"
                                f"Content-Length: {len(content)}\r\n"
                                "Connection: close\r\n"
                                "\r\n"
                            ).encode('utf-8') + content.encode('utf-8')
                            
                            ssl_client.send(response)
                            ssl_client.close()
                        except:
                            pass
                    else:
                        response = (
                            "HTTP/1.1 200 OK\r\n"
                            "Content-Type: text/html\r\n"
                            f"Content-Length: {len(content)}\r\n"
                            "Connection: close\r\n"
                            "\r\n"
                        ).encode('utf-8') + content.encode('utf-8')
                        
                        client_socket.send(response)
                    
                    return True
        
        return False
    
    def serve_not_found_page(self, client_socket, host, is_https=False):
        """Serve the not-found page"""
        print(f"📄 Serving not-found page for: {host}")
        
        not_found_html = self.get_not_found_page(host)
        
        if is_https:
            try:
                client_socket.send(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(CERT_FILE, KEY_FILE)
                
                ssl_client = context.wrap_socket(client_socket, server_side=True)
                
                response = (
                    "HTTP/1.1 404 Not Found\r\n"
                    "Content-Type: text/html\r\n"
                    f"Content-Length: {len(not_found_html)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode('utf-8') + not_found_html.encode('utf-8')
                
                ssl_client.send(response)
                ssl_client.close()
            except:
                pass
        else:
            response = (
                "HTTP/1.1 404 Not Found\r\n"
                "Content-Type: text/html\r\n"
                f"Content-Length: {len(not_found_html)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode('utf-8') + not_found_html.encode('utf-8')
            
            try:
                client_socket.send(response)
            except:
                pass
    
    def rewrite_links_for_proxy(self, html_content, base_url):
        """Rewrite links to work with our proxy"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            base_domain = urlparse(base_url).netloc

            # Rewrite resource links
            for tag, attr in [('script', 'src'), ('link', 'href'), ('img', 'src')]:
                for element in soup.find_all(tag, **{attr: True}):
                    url = element[attr]
                    if url and not url.startswith(('data:', 'blob:', 'javascript:', '#')):
                        if url.startswith(('http://', 'https://')):
                            # Already absolute, pass through proxy
                            element[attr] = url
                        elif url.startswith('/'):
                            # Absolute path - make absolute URL
                            full_url = f"http://{base_domain}{url}"
                            element[attr] = full_url
                        else:
                            # Relative path
                            full_url = urljoin(base_url, url)
                            element[attr] = full_url

            return str(soup)
        except:
            return html_content
    
    def forward_proxy_connect(self, request, client_socket, host_port):
        """Forward HTTPS CONNECT to real server"""
        try:
            if ':' in host_port:
                host, port = host_port.split(':')
                port = int(port)
            else:
                host = host_port
                port = 443
            
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.settimeout(10)
            
            try:
                remote_ip = socket.gethostbyname(host)
                remote.connect((remote_ip, port))
                
                client_socket.send(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.forward_data(client_socket, remote)
                
            except Exception as e:
                print(f"❌ Forward connect error to {host}: {e}")
            finally:
                remote.close()
                
        except Exception as e:
            print(f"❌ Forward connect error: {e}")
    
    def forward_proxy_http(self, request, client_socket):
        """Forward HTTP request to real server"""
        try:
            request_str = request.decode('utf-8', errors='ignore')
            host = self.extract_host(request_str)
            
            if not host:
                client_socket.send(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.settimeout(10)
            
            try:
                remote_ip = socket.gethostbyname(host)
                remote.connect((remote_ip, 80))
                remote.send(request)
                
                while True:
                    data = remote.recv(8192)
                    if not data:
                        break
                    client_socket.send(data)
                    
            except Exception as e:
                print(f"❌ Forward HTTP error to {host}: {e}")
                try:
                    client_socket.send(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                except:
                    pass
            finally:
                remote.close()
                
        except Exception as e:
            print(f"❌ Forward HTTP error: {e}")
    
    def forward_data(self, client, remote):
        """Bidirectional data forwarding"""
        sockets = [client, remote]
        
        while sockets:
            try:
                readable, _, _ = select.select(sockets, [], [], 1)
                
                for sock in readable:
                    other = remote if sock is client else client
                    
                    try:
                        data = sock.recv(8192)
                        if not data:
                            sockets.remove(sock)
                            sockets.remove(other)
                            break
                        other.send(data)
                    except:
                        sockets.remove(sock)
                        sockets.remove(other)
                        break
                        
            except:
                break
    
    def extract_host(self, request_str):
        """Extract host from HTTP request"""
        lines = request_str.split('\r\n')
        for line in lines:
            if line.lower().startswith('host:'):
                host = line[5:].strip()
                if ':' in host:
                    host = host.split(':')[0]
                return host
        return None
    
    def start_browser(self):
        """Start the browser server in a separate thread"""
        def browser_thread():
            try:
                from http.server import HTTPServer
                
                # Create a request handler that uses our offline browser
                class BrowserRequestHandler(BaseHTTPRequestHandler):
                    offline_browser = None
                    unified_server = None
                    
                    def do_GET(self):
                        try:
                            path = unquote(self.path)
                            
                            if '?' in path:
                                path = path.split('?')[0]
                            
                            print(f"🌐 Browser request: {path}")
                            
                            if path == '/' or path == '/index.html':
                                self.serve_index()
                            elif path == '/youtube' or path == '/youtube/':
                                self.serve_youtube_index()
                            elif path.startswith('/temp_videos/'):
                                self.serve_temp_video(path)
                            elif path.startswith('/youtube/'):
                                self.serve_youtube_video(path)
                            elif path.startswith('/page/'):
                                self.serve_saved_page(path)
                            elif path.startswith('/asset/'):
                                self.serve_asset(path)
                            else:
                                # Try to find a matching page
                                self.serve_saved_page(f'/page{path}')
                                
                        except (ConnectionResetError, ConnectionAbortedError):
                            # Client disconnected, ignore
                            pass
                        except Exception as e:
                            print(f"❌ Browser error: {e}")
                            try:
                                self.send_error(500, str(e))
                            except:
                                pass
                    
                    def serve_index(self):
                        """Serve the main index page"""
                        self.send_response(200)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        
                        regular_sites = {k: v for k, v in self.offline_browser.loaded_sites.items() 
                                        if not v.get('is_youtube', False)}
                        youtube_sites = self.offline_browser.youtube_videos
                        
                        # Get local IP from unified server
                        local_ip = self.unified_server.local_ip if self.unified_server else "localhost"
                        proxy_port = self.unified_server.proxy_port if self.unified_server else "8080"
                        
                        html = f"""
                        <!DOCTYPE html>
                        <html>
                        <head>
                            <title>📚 Offline Website Browser</title>
                            <meta charset="UTF-8">
                            <meta name="viewport" content="width=device-width, initial-scale=1.0">
                            <style>
                                * {{
                                    margin: 0;
                                    padding: 0;
                                    box-sizing: border-box;
                                }}
                                
                                body {{
                                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                    min-height: 100vh;
                                    padding: 20px;
                                }}
                                
                                .container {{
                                    max-width: 1200px;
                                    margin: 0 auto;
                                }}
                                
                                .header {{
                                    background: rgba(255, 255, 255, 0.95);
                                    padding: 40px;
                                    border-radius: 20px;
                                    margin-bottom: 30px;
                                    box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                                    text-align: center;
                                }}
                                
                                .header h1 {{
                                    font-size: 3em;
                                    background: linear-gradient(135deg, #667eea, #764ba2);
                                    -webkit-background-clip: text;
                                    -webkit-text-fill-color: transparent;
                                    margin-bottom: 10px;
                                }}
                                
                                .tabs {{
                                    display: flex;
                                    gap: 10px;
                                    margin-bottom: 20px;
                                    justify-content: center;
                                }}
                                
                                .tab-btn {{
                                    padding: 10px 20px;
                                    background: rgba(255, 255, 255, 0.8);
                                    border: none;
                                    border-radius: 20px;
                                    cursor: pointer;
                                    font-size: 1em;
                                    transition: all 0.3s ease;
                                }}
                                
                                .tab-btn.active {{
                                    background: #4CAF50;
                                    color: white;
                                }}
                                
                                .tab-btn.youtube.active {{
                                    background: #ff0000;
                                }}
                                
                                .tab-content {{
                                    display: none;
                                }}
                                
                                .tab-content.active {{
                                    display: block;
                                }}
                                
                                .sites-grid {{
                                    display: grid;
                                    grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
                                    gap: 25px;
                                }}
                                
                                .site-card, .video-card {{
                                    background: rgba(255, 255, 255, 0.95);
                                    padding: 30px;
                                    border-radius: 15px;
                                    box-shadow: 0 15px 35px rgba(0,0,0,0.1);
                                    transition: transform 0.3s ease;
                                }}
                                
                                .site-card:hover, .video-card:hover {{
                                    transform: translateY(-5px);
                                }}
                                
                                .site-card h2 a {{
                                    color: #333;
                                    text-decoration: none;
                                    font-size: 1.4em;
                                }}
                                
                                .stats {{
                                    display: flex;
                                    gap: 15px;
                                    margin: 15px 0;
                                }}
                                
                                .stat {{
                                    background: linear-gradient(135deg, #667eea, #764ba2);
                                    color: white;
                                    padding: 8px 15px;
                                    border-radius: 20px;
                                    font-size: 0.9em;
                                }}
                                
                                .youtube-btn {{
                                    background: #ff0000;
                                    color: white;
                                    padding: 8px 15px;
                                    border-radius: 20px;
                                    text-decoration: none;
                                    display: inline-block;
                                    margin-top: 10px;
                                }}
                                
                                .pages-list {{
                                    max-height: 200px;
                                    overflow-y: auto;
                                    margin-top: 15px;
                                }}
                                
                                .pages-list ul {{
                                    list-style: none;
                                }}
                                
                                .pages-list li {{
                                    margin-bottom: 8px;
                                    padding: 8px 12px;
                                    background: #f8f9fa;
                                    border-radius: 8px;
                                }}
                                
                                .pages-list a {{
                                    color: #495057;
                                    text-decoration: none;
                                    display: block;
                                }}
                                
                                .info-box {{
                                    background: #e3f2fd;
                                    border-left: 4px solid #2196f3;
                                    padding: 15px;
                                    margin-bottom: 20px;
                                    border-radius: 5px;
                                }}
                            </style>
                            <script>
                                function showTab(tabName) {{
                                    document.querySelectorAll('.tab-content').forEach(tab => {{
                                        tab.classList.remove('active');
                                    }});
                                    
                                    document.getElementById(tabName).classList.add('active');
                                    
                                    document.querySelectorAll('.tab-btn').forEach(btn => {{
                                        btn.classList.remove('active');
                                    }});
                                    event.target.classList.add('active');
                                }}
                            </script>
                        </head>
                        <body>
                            <div class="container">
                                <div class="header">
                                    <h1>📚 Offline Website Browser</h1>
                                    <p>Browse your downloaded content or intercept new sites!</p>
                                </div>
                                
                                <div class="info-box">
                                    <strong>💡 Proxy Info:</strong> Set your device proxy to <code>{local_ip}:{proxy_port}</code> to intercept example.com or access offline content automatically!
                                </div>
                                
                                <div class="tabs">
                                    <button class="tab-btn active" onclick="showTab('websites')">🌐 Websites ({len(regular_sites)})</button>
                                    <button class="tab-btn youtube" onclick="showTab('youtube')">🎬 YouTube ({len(youtube_sites)})</button>
                                </div>
                                
                                <div id="websites" class="tab-content active">
                        """
                        
                        if regular_sites:
                            html += '<div class="sites-grid">'
                            for domain, site_data in regular_sites.items():
                                html += f"""
                                <div class="site-card">
                                    <h2><a href="/page/{domain}">{domain}</a></h2>
                                    <div class="stats">
                                        <span class="stat">📄 {len(site_data['pages'])} pages</span>
                                        <span class="stat">🎨 {len(site_data['assets'])} assets</span>
                                    </div>
                                    <div class="pages-list">
                                        <strong>Available Pages:</strong>
                                        <ul>
                                """
                                
                                for page_url in list(site_data['pages'].keys())[:8]:
                                    page_name = urlparse(page_url).path or '/'
                                    if len(page_name) > 40:
                                        page_name = page_name[:37] + '...'
                                    html += f'<li><a href="/page/{page_url}">{page_name}</a></li>'
                                
                                if len(site_data['pages']) > 8:
                                    html += f'<li>... and {len(site_data["pages"]) - 8} more pages</li>'
                                
                                html += """
                                        </ul>
                                    </div>
                                </div>
                                """
                            html += '</div>'
                        else:
                            html += """
                                <div class="empty-state" style="text-align: center; padding: 60px;">
                                    <h3>No regular websites loaded</h3>
                                    <p>Run the downloader first or configure proxy to intercept sites!</p>
                                </div>
                            """
                        
                        html += """
                                </div>
                                
                                <div id="youtube" class="tab-content">
                        """
                        
                        if youtube_sites:
                            html += '<div class="sites-grid">'
                            for video in sorted(youtube_sites, key=lambda x: x['title']):
                                html += f"""
                                <div class="video-card">
                                    <h3>{video['title']}</h3>
                                    <div class="stats">
                                        <span class="stat" style="background: #ff0000;">🎬 YouTube</span>
                                        <span class="stat" style="background: #ff0000;">{video['channel']}</span>
                                    </div>
                                    <a href="/youtube/{video['domain']}" class="youtube-btn">▶ Watch Video</a>
                                </div>
                                """
                            html += '</div>'
                        else:
                            html += """
                                <div class="empty-state" style="text-align: center; padding: 60px;">
                                    <h3>No YouTube videos loaded</h3>
                                    <p>Download some videos first!</p>
                                </div>
                            """
                        
                        html += """
                                </div>
                            </div>
                        </body>
                        </html>
                        """
                        
                        self.wfile.write(html.encode('utf-8'))
                    
                    def serve_youtube_index(self):
                        """Serve YouTube index (redirect to main with tab)"""
                        self.send_response(302)
                        self.send_header('Location', '/#youtube')
                        self.end_headers()
                    
                    def serve_temp_video(self, path):
                        """Serve video files from temp directory"""
                        try:
                            video_filename = path.replace('/temp_videos/', '')
                            video_path = os.path.join(self.offline_browser.temp_dir, video_filename)
                            
                            if not os.path.exists(video_path):
                                self.send_error(404)
                                return
                            
                            file_size = os.path.getsize(video_path)
                            range_header = self.headers.get('Range', '')
                            
                            with open(video_path, 'rb') as f:
                                if range_header:
                                    range_match = re.search(r'bytes=(\d+)-(\d*)', range_header)
                                    if range_match:
                                        range_start = int(range_match.group(1))
                                        range_end = range_match.group(2)
                                        range_end = int(range_end) if range_end else file_size - 1
                                        
                                        self.send_response(206)
                                        self.send_header('Content-type', 'video/mp4')
                                        self.send_header('Content-Range', f'bytes {range_start}-{range_end}/{file_size}')
                                        self.send_header('Content-Length', str(range_end - range_start + 1))
                                        self.send_header('Accept-Ranges', 'bytes')
                                        self.end_headers()
                                        
                                        f.seek(range_start)
                                        remaining = range_end - range_start + 1
                                        chunk_size = 8192
                                        
                                        while remaining > 0:
                                            chunk = f.read(min(chunk_size, remaining))
                                            if not chunk:
                                                break
                                            self.wfile.write(chunk)
                                            remaining -= len(chunk)
                                else:
                                    self.send_response(200)
                                    self.send_header('Content-type', 'video/mp4')
                                    self.send_header('Content-Length', str(file_size))
                                    self.send_header('Accept-Ranges', 'bytes')
                                    self.end_headers()
                                    
                                    chunk_size = 8192
                                    while True:
                                        chunk = f.read(chunk_size)
                                        if not chunk:
                                            break
                                        self.wfile.write(chunk)
                                        
                        except (ConnectionResetError, ConnectionAbortedError):
                            # Client disconnected, ignore
                            pass
                        except Exception as e:
                            print(f"❌ Error serving video: {e}")
                    
                    def serve_youtube_video(self, path):
                        """Serve a YouTube video page"""
                        video_domain = path[9:]  # Remove '/youtube/'
                        
                        for video in self.offline_browser.youtube_videos:
                            if video['domain'] == video_domain:
                                site_data = self.offline_browser.loaded_sites.get(video_domain)
                                if site_data:
                                    page_data = next(iter(site_data['pages'].values()))
                                    content = page_data['content']
                                    
                                    self.send_response(200)
                                    self.send_header('Content-type', 'text/html; charset=utf-8')
                                    self.end_headers()
                                    self.wfile.write(content.encode('utf-8'))
                                    return
                        
                        self.send_error(404)
                    
                    def serve_saved_page(self, path):
                        """Serve a page from offline storage"""
                        requested_url = path[6:]  # Remove '/page/'
                        
                        page_data = self.offline_browser.find_page_by_url(requested_url)
                        
                        if page_data:
                            content = page_data['content']
                            
                            # Rewrite links for offline browsing
                            soup = BeautifulSoup(content, 'html.parser')
                            
                            # Update resource links to use our asset server
                            for tag, attr in [('script', 'src'), ('link', 'href'), ('img', 'src')]:
                                for element in soup.find_all(tag, **{attr: True}):
                                    url = element[attr]
                                    if url and not url.startswith(('data:', 'blob:', 'javascript:', '#')):
                                        if url.startswith(('http://', 'https://')):
                                            element[attr] = f"/asset/{url}"
                                        elif url.startswith('/'):
                                            full_url = urljoin(page_data['url'], url)
                                            element[attr] = f"/asset/{full_url}"
                                        else:
                                            full_url = urljoin(page_data['url'], url)
                                            element[attr] = f"/asset/{full_url}"
                            
                            self.send_response(200)
                            self.send_header('Content-type', page_data.get('content_type', 'text/html'))
                            self.end_headers()
                            self.wfile.write(str(soup).encode('utf-8'))
                        else:
                            self.send_error(404)
                    
                    def serve_asset(self, path):
                        """Serve an asset file"""
                        asset_url = path[7:]  # Remove '/asset/'
                        
                        asset_data = self.offline_browser.find_asset_by_url(asset_url)
                        
                        if asset_data:
                            content_type = asset_data.get('content_type', 'application/octet-stream')
                            encoding = asset_data.get('encoding', 'text')
                            content = asset_data['content']
                            
                            self.send_response(200)
                            self.send_header('Content-type', content_type)
                            self.send_header('Cache-Control', 'public, max-age=3600')
                            
                            if encoding == 'base64':
                                binary_content = base64.b64decode(content)
                                self.send_header('Content-Length', str(len(binary_content)))
                                self.end_headers()
                                self.wfile.write(binary_content)
                            else:
                                self.send_header('Content-Length', str(len(content)))
                                self.end_headers()
                                self.wfile.write(content.encode('utf-8'))
                        else:
                            self.send_error(404)
                    
                    def log_message(self, format, *args):
                        """Reduce log spam"""
                        pass
                
                # Set up the handler with references to our data
                BrowserRequestHandler.offline_browser = self.offline_browser
                BrowserRequestHandler.unified_server = self
                
                # Start server
                server = HTTPServer(('0.0.0.0', self.browser_port), BrowserRequestHandler)
                print(f"🌐 Browser server started on port {self.browser_port}")
                print(f"   → Local: http://localhost:{self.browser_port}")
                print(f"   → Network: http://{self.local_ip}:{self.browser_port}")
                
                # Open browser automatically
                try:
                    webbrowser.open(f'http://localhost:{self.browser_port}')
                except:
                    pass
                
                server.serve_forever()
                
            except Exception as e:
                print(f"❌ Browser server error: {e}")
        
        thread = threading.Thread(target=browser_thread, daemon=True)
        thread.start()
        return thread
    
    def start(self):
        """Start both proxy and browser servers"""
        print("="*70)
        print("🚀 UNIFIED OFFLINE BROWSER & INTERCEPTOR")
        print("="*70)
        print("✓ Intercepts example.com → shows 'lol'")
        print("✓ Serves offline content automatically")
        print("✓ Shows helpful page when content not found")
        print("✓ Built-in browser for offline content")
        print("="*70)
        
        print(f"\n📡 Your IP: {self.local_ip}")
        print(f"🔄 Proxy port: {self.proxy_port}")
        print(f"🌐 Browser port: {self.browser_port}")
        
        # Start proxy
        proxy_thread = self.start_proxy()
        
        # Start browser
        browser_thread = self.start_browser()
        
        print("\n" + "="*70)
        print("✅ BOTH SERVERS ARE RUNNING!")
        print("="*70)
        print("\n📱 ON YOUR PHONE:")
        print(f"1. Connect to PC's WiFi")
        print(f"2. Set MANUAL PROXY to {self.local_ip}:{self.proxy_port}")
        print("3. Try these:")
        print("   • http://example.com → shows 'lol'")
        print("   • Any downloaded site → shows offline content")
        print("   • Unknown site → shows helpful page with links")
        print("\n💻 ON THIS PC:")
        print(f"• Browser: http://localhost:{self.browser_port}")
        print("• Proxy is already configured (if you set it)")
        print("\n🎯 BEHAVIOR:")
        print("• example.com → intercepted with 'lol'")
        print("• Downloaded sites → served offline")
        print("• Unknown sites → helpful 'not downloaded' page")
        print("• YouTube videos → play offline")
        print("\nPress Ctrl+C to stop everything")
        print("="*70)
        
        try:
            # Keep main thread alive
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n👋 Shutting down...")
            self.proxy_running = False
            self.browser_running = False
            time.sleep(2)
            print("✅ Cleanup complete!")

def setup_windows_proxy():
    """Configure Windows proxy"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE
        )
        
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{PROXY_PORT}")
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.0.0.1;*.local")
        winreg.CloseKey(key)
        
        # Notify system
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
        print(f"✅ Windows proxy set to 127.0.0.1:{PROXY_PORT}")
        return True
    except Exception as e:
        print(f"❌ Failed to set proxy: {e}")
        return False

def disable_windows_proxy():
    """Disable Windows proxy"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
        print("✅ Windows proxy disabled")
    except:
        pass

def kill_port(port):
    """Kill process using port"""
    try:
        result = subprocess.run(
            f'netstat -ano | findstr :{port}',
            shell=True, capture_output=True, text=True
        )
        for line in result.stdout.split('\n'):
            if 'LISTENING' in line:
                parts = line.strip().split()
                if len(parts) > 4:
                    pid = parts[-1]
                    subprocess.run(f'taskkill /PID {pid} /F', shell=True, capture_output=True)
    except:
        pass

def is_admin():
    """Check admin rights"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    """Relaunch as admin"""
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Unified Offline Browser & Interceptor')
    parser.add_argument('--proxy-port', type=int, default=8080, help='Proxy server port')
    parser.add_argument('--browser-port', type=int, default=8000, help='Browser server port')
    parser.add_argument('--no-proxy-setup', action='store_true', help="Don't auto-configure Windows proxy")
    parser.add_argument('--directory', help='Directory containing .page files')
    
    args = parser.parse_args()
    
    # Check for required packages
    try:
        from OpenSSL import crypto
        from bs4 import BeautifulSoup
    except ImportError as e:
        print(f"\n📦 Installing required package: {e.name}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyopenssl", "beautifulsoup4"])
        from OpenSSL import crypto
        from bs4 import BeautifulSoup
    
    if not is_admin():
        print("\n⚠️  Administrator privileges required for proxy setup!")
        run_as_admin()
        return
    
    print("\n🔧 Cleaning up ports...")
    kill_port(args.proxy_port)
    kill_port(args.browser_port)
    kill_port(80)
    kill_port(443)
    time.sleep(2)
    
    print("\n🔧 Creating self-signed certificate...")
    create_self_signed_cert()
    
    if not args.no_proxy_setup:
        print("\n🔧 Configuring Windows proxy...")
        setup_windows_proxy()
    
    # Create and start unified server
    server = UnifiedProxyAndBrowser(
        proxy_port=args.proxy_port,
        browser_port=args.browser_port
    )
    
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        if not args.no_proxy_setup:
            disable_windows_proxy()

if __name__ == "__main__":
    main()