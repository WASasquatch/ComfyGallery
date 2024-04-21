import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import sys
import urllib
from datetime import datetime
from PIL import Image, PngImagePlugin

import importlib
import requests
from aiohttp import web
from tqdm import tqdm
import webbrowser


# SERVERs
DOMAIN = '127.0.0.1'
PORT = 8189
ROOT = os.path.dirname(os.path.abspath(__file__))

# FEATURES
NO_BROWSER = False
PURGE_CACHE = False

# GENERAL GLOBALS
ALLOWED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp"]
CP_FILE = 'web'+os.sep+'extensions'+os.sep+'core'+os.sep+'colorPalette.js'
DB_CACHED = False
IMAGE_PATHS = [
    "output",
    "input"
]
THUMBNAIL_DIRECTORY = os.path.join(ROOT, "temp")
TITLE = "ComfyGallery"


# FUNCTIONS
class cstr(str):
    class color:
        END = '\033[0m'
        BOLD, ITALIC, UNDERLINE, BLINK, BLINK2, SELECTED = ['\033[%dm' % (i,) for i in range(1, 7)]
        BLACK, RED, GREEN, YELLOW, BLUE, VIOLET, BEIGE, WHITE = ['\033[%dm' % (i,) for i in range(30, 38)]
        BLACKBG, REDBG, GREENBG, YELLOWBG, BLUEBG, VIOLETBG, BEIGEBG, WHITEBG = ['\033[%dm' % (i,) for i in range(40, 48)]
        GREY, LIGHTRED, LIGHTGREEN, LIGHTYELLOW, LIGHTBLUE, LIGHTVIOLET, LIGHTBEIGE, LIGHTWHITE = ['\033[%dm' % (i,) for i in range(90, 98)]
        GREYBG, LIGHTREDBG, LIGHTGREENBG, LIGHTYELLOWBG, LIGHTBLUEBG, LIGHTVIOLETBG, LIGHTBEIGEBG, LIGHTWHITEBG = ['\033[%dm' % (i,) for i in range(100, 108)]
        @staticmethod
        def add_code(name, code):
            if not hasattr(cstr.color, name.upper()):
                setattr(cstr.color, name.upper(), code)
            else:
                raise ValueError(f"'cstr' object already contains a code with the name '{name}'.")

    def __new__(cls, text):
        return super().__new__(cls, text)

    def __getattr__(self, attr):
        if attr.lower().startswith("_cstr"):
            code = getattr(self.color, attr.upper().lstrip("_cstr"))
            modified_text = self.replace(f"__{attr[1:]}__", f"{code}")
            return cstr(modified_text)
        elif attr.upper() in dir(self.color):
            code = getattr(self.color, attr.upper())
            modified_text = f"{code}{self}{self.color.END}"
            return cstr(modified_text)
        elif attr.lower() in dir(cstr):
            return getattr(cstr, attr.lower())
        else:
            raise AttributeError(f"'cstr' object has no attribute '{attr}'")

    def print(self, **kwargs):
        print(self, **kwargs)
        
#! MESSAGE TEMPLATES
cstr.color.add_code("msg", f"{cstr.color.LIGHTVIOLET}ComfyGallery{cstr.color.END}: ")
cstr.color.add_code("access", f"{cstr.color.LIGHTVIOLET}ComfyGallery{cstr.color.END} ({cstr.color.LIGHTYELLOW}Access-Log{cstr.color.END}): ")
cstr.color.add_code("warning", f"{cstr.color.LIGHTVIOLET}ComfyGallery{cstr.color.END} {cstr.color.LIGHTYELLOW}Warning: {cstr.color.END}")
cstr.color.add_code("error", f"{cstr.color.LIGHTVIOLET}ComfyGallery{cstr.color.END} {cstr.color.RED}Error: {cstr.color.END}")

def window_title(title):
    ctypes.windll.kernel32.SetConsoleTitleW(title)

def get_full_path(category, path):
    full_path = None
    for base_path in IMAGE_PATHS:
        if os.path.basename(base_path) == category:
            base_folder = base_path
            full_path = os.path.join(base_folder, path)
            break
    
    if not full_path:
        cstr(f"Unable to determine image path from category {cstr.color.LIGHTYELLOW}{category}{cstr.color.END} and path {cstr.color.BOLD}{path}{cstr.color.END}").error.print()
        return None
        
    if not os.path.exists(full_path):
        cstr(f"Unable to find image at requested path: {cstr.color.BOLD}{full_path}{cstr.color.END}").error.print()
        return None
        
    return full_path
    
def filter_arguments(allowed_args):
    filtered_args = [arg for arg in sys.argv if arg in allowed_args]
    sys.argv = [sys.argv[0]] + filtered_args

def get_color_palettes(path):
    if os.path.exists(path):
        with open(path, 'r') as file:
            theme_code = file.read()
    else:
        with open(os.path.join(ROOT, 'cg_default_themes.json'), 'r') as file:
            theme_code = file.read()
            color_palettes = json.loads(theme_code)
            cstr(f"Unable to locate ComfyUI color palette file at {cstr.color.BOLD}{path}{cstr.color.END}, defaulting to built-in light/dark themes.").warning.print()
            return color_palettes
    match = re.search(r'const colorPalettes = ({.*?});', theme_code, re.DOTALL)
    if match:
        color_palettes = match.group(1)
        color_palettes = re.sub(r'\s\/\/\s.*$', '', color_palettes, flags=re.MULTILINE)
        color_palettes = re.sub(r',(\s*?})', r'\1', color_palettes)
        color_palettes = json.loads(color_palettes)          
        return color_palettes
    return {}
    
def split_paths(arg):
    paths = arg.split(',')
    paths = [path.strip() for path in paths]
    return paths

def create_cors_middleware(allowed_origin: str):
    @web.middleware
    async def cors_middleware(request: web.Request, handler):
        if request.method == "OPTIONS":
            # Pre-flight request. Reply successfully:
            response = web.Response()
        else:
            response = await handler(request)

        response.headers['Access-Control-Allow-Origin'] = allowed_origin
        response.headers['Access-Control-Allow-Methods'] = 'POST, GET, DELETE, PUT, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response

    return cors_middleware

def get_paths(category, path):
    result = {
        "directories": [],
        "images": []
    }

    for image_path in IMAGE_PATHS:
        if os.path.basename(image_path) == category:
            base_folder = image_path
            path = path if not path.startswith('/') else ''
            path = path.replace('/', os.path.sep)
            target_folder = os.path.join(base_folder, path)
            combined_path = os.path.abspath(target_folder)

            if not combined_path.startswith(base_folder):
                return json.dumps(result, indent=4)

            try:
                if not path or path == "":
                    for item in os.listdir(image_path):
                        item_path = os.path.join(image_path, item)
                        if os.path.isdir(item_path):
                            relative_path = os.path.relpath(item_path, base_folder).replace("\\", "/")
                            result["directories"].append(relative_path)
                        else:
                            if os.path.splitext(item)[1] in ['.png', '.webp', '.jpg', '.jpeg', '.gif']:
                                image_relative_path = os.path.join(path, item).replace("\\", "/")
                                result["images"].append(image_relative_path)
                else:
                    for item in os.listdir(combined_path):
                        item_path = os.path.join(combined_path, item)
                        if os.path.isdir(item_path):
                            relative_path = os.path.relpath(item_path, base_folder).replace("\\", "/")
                            result["directories"].append(relative_path)
                        else:
                            if os.path.splitext(item)[1] in ['.png', '.webp', '.jpg', '.jpeg', '.gif']:
                                image_relative_path = os.path.join(path, item).replace("\\", "/")
                                result["images"].append(image_relative_path)
            except Exception as e:
                cstr(f"There was an error with a path request for category `{category}` and path `{path}`").error.print()
                print(e)

            break
        
        if result['directories']:
            result['directories'] = sorted(result['directories'])
        if result['images']:
            result['images'] = sorted(result['images'])

    return json.dumps(result)

def compress_image(category, path):
    full_path = get_full_path(category, path)
    if full_path:
        with open(full_path, "rb") as image_file:
            image_data = image_file.read()
        
        thumbnail_filename = os.path.basename(full_path)
        thumbnail_extension = os.path.splitext(thumbnail_filename)[1]
        hash_object = hashlib.sha256(image_data)
        hash_hex = hash_object.hexdigest()
        cleaned_hash = re.sub(r"[^a-zA-Z0-9]", "", hash_hex)
        thumbnail_filename = f"{thumbnail_filename}_{cleaned_hash}{thumbnail_extension}"
        thumbnail_path = os.path.join(THUMBNAIL_DIRECTORY, thumbnail_filename)

        if os.path.exists(thumbnail_path):
            with open(thumbnail_path, "rb") as thumbnail_file:
                return thumbnail_file.read()

        try:
        
            image = Image.open(full_path)

            if image.mode != "RGB":
                image = image.convert("RGB")

            width, height = image.size
            aspect_ratio = min(200 / width, 400 / height)
            new_width = int(width * aspect_ratio)
            new_height = int(height * aspect_ratio)
            resized_image = image.resize((new_width, new_height), Image.Resampling(1))
            output_buffer = io.BytesIO()
            resized_image.save(output_buffer, "JPEG", quality=90)
            compressed_bytes = output_buffer.getvalue()
            output_buffer.close()

            with open(thumbnail_path, "wb") as thumbnail_file:
                thumbnail_file.write(compressed_bytes)

            return compressed_bytes
            
        except OSError as e:
            cstr(f"The image from category {cstr.color.LIGHTYELLOW}{category}{cstr.color.END} at {cstr.color.BOLD}{path}{cstr.color.END} may be corrupted.").error.print()
            print(f"Error Message: {e}")
        
    return b''

# ROUTE FUNCTIONS

# GET DIRECTORY PATHS
async def get_directory(request):
    category = request.query.get("category")
    path = request.query.get("path")
    category = urllib.parse.unquote(category) if category else None
    path = urllib.parse.unquote(path) if path else None

    if category is None or path is None:
        cstr("A request for folder paths is being made without query paramters.").warning.print()
        return web.Response(text="Missing query parameters 'category' or 'path'", status=400)

    json_result = get_paths(category, path)
    return web.json_response(json_result)
  
# GET IMAGE THUMBNAIL  
async def get_image(request):
    category = request.query.get("category")
    path = request.query.get("path")
    category = urllib.parse.unquote(category) if category else None
    path = urllib.parse.unquote(path) if path else None
    compressed_bytes = compress_image(category, path)
    response = web.StreamResponse()
    response.content_type = 'image/jpeg'
    response.content_length = len(compressed_bytes)
    await response.prepare(request)
    await response.write(compressed_bytes)
    await response.write_eof()

    return response
    
# SEARCH IMAGES
async def search_images(request):
    def search_image_paths(search_query):
        results = {"images": []}

        for path in IMAGE_PATHS:
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        if is_valid_image(file_path):
                            matched_objects = search_query_in_filename(file, search_query) or search_query_in_workflow(file_path, search_query)
                            if matched_objects:
                                result = {
                                    "category": os.path.basename(path),
                                    "path": os.path.relpath(file_path, path),
                                    "matched": matched_objects[1][1] if len(matched_objects) > 1 and len(matched_objects[1]) > 1 else matched_objects,
                                }
                                results["images"].append(result)

        return results

    def is_valid_image(file_path):
        _, extension = os.path.splitext(file_path)
        return extension.lower() in ALLOWED_EXTENSIONS

    def search_query_in_filename(filename, search_query):
        matched_objects = []
        for query in search_query.split():
            if query.lower() in filename.lower():
                matched_objects.append(([], None))
        return matched_objects

    def search_query_in_workflow(file_path, search_query):
        matched_objects = []
        try:
            image = Image.open(file_path)
            workflow = image.text.get("workflow")
            if workflow:
                json_obj = json.loads(workflow)
                matched_objects = is_search_query_match(json_obj, search_query, [])
        except Exception as e:
            pass
        return matched_objects

    def is_search_query_match(json_obj, search_query, path):
        matched_objects = []
        if isinstance(json_obj, dict):
            for key, value in json_obj.items():
                new_path = path + [key]
                if is_match(key, value, search_query):
                    matched_objects.append((new_path, json_obj))
                matched_objects.extend(is_search_query_match(value, search_query, new_path))
        elif isinstance(json_obj, list):
            for index, item in enumerate(json_obj):
                new_path = path + [index]
                matched_objects.extend(is_search_query_match(item, search_query, new_path))
        elif isinstance(json_obj, tuple):
            for index, item in enumerate(json_obj):
                new_path = path + [index]
                matched_objects.extend(is_search_query_match(item, search_query, new_path))
        return matched_objects

    def is_match(key, value, search_query):
        term = search_query.lower()
        return term in str(key).lower() or term in str(value).lower()

    query = request.query.get("query")
    query = urllib.parse.unquote(query) if query else None
    
    result = {"images": []}
    
    if query:
        result = search_image_paths(query)

    return web.Response(text=json.dumps(result), content_type='application/json')
    
# DELETE IMAGE
last_image = ()
async def delete_image(request):
    global last_image
    category = request.query.get("category")
    path = request.query.get("path")
    category = urllib.parse.unquote(category) if category else None
    path = urllib.parse.unquote(path) if path else None

    if last_image:
        if last_image == (category, path):
            return web.Response(text=json.dumps({"success":True}), content_type='application/json')
            
    last_image = (category, path)
    full_path = get_full_path(category, path)

    try:
        os.remove(full_path)
        cstr(f"Successfully deleted file: {full_path}").msg.print()
        return web.Response(text=json.dumps({"success":True}), content_type='application/json')
    except FileNotFoundError:
        pass
    except Exception as e:
        cstr(f"An error occurred while deleting file: {full_path}").error.print()
        print(e)
    return web.Response(text=json.dumps({"success":False}), content_type='application/json')
    
# GET WORKFLOWS
last_image = None
async def get_workflow(request):
    global last_image
    category = request.query.get("category")
    path = request.query.get("path")
    category = urllib.parse.unquote(category) if category else None
    path = urllib.parse.unquote(path) if path else None
    full_path = get_full_path(category, path)
    
    image = Image.open(full_path)
    
    workflow = {}
    
    if hasattr(image, 'text'):
        if 'workflow' in image.text:
            workflow = json.loads(image.text['workflow'])

    return web.Response(text=json.dumps(workflow, indent=4), content_type='text/plain')

        
    
# GET FAV ICON SVG
async def get_fav_icon(request):
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" shape-rendering="geometricPrecision" image-rendering="optimizeQuality" fill-rule="evenodd" xmlns:v="https://vecta.io/nano"><path fill="#C1344E" d="M816.5 139.5c.762 1.762 2.096 2.762 4 3 125.583 111.584 181.75 251.584 168.5 420-21.063 159.595-99.897 281.095-236.5 364.5-102.486 57.637-211.82 76.64-328 57C286.266 955.677 179.1 882.51 103 764.5c-29.733-48.125-50.567-99.792-62.5-155-27.685-151.595 6.481-287.262 102.5-407C248.583 82.322 381.75 25.489 542.5 32c102.956 7.351 194.29 43.185 274 107.5zm71 331c7.284 56.848.117 111.848-21.5 165-19.757 51.712-49.09 97.046-88 136-64.708 66.104-143.208 105.271-235.5 117.5-50.532 4.638-99.532-2.028-147-20-33.858-12.091-65.191-28.591-94-49.5-.473-1.406-1.473-2.073-3-2 0-.667-.333-1-1-1a608.67 608.67 0 0 1-67.5-70c-42.768-57.276-68.102-121.609-76-193-2.956-46.221 4.044-90.887 21-134 15.195-41.082 36.528-78.415 64-112 32.099-38.788 69.599-70.955 112.5-96.5 35.984-19.662 73.984-33.995 114-43 56.31-10.79 111.31-6.123 165 14 50.397 17.513 95.064 44.18 134 80 27.031 25.32 50.864 53.154 71.5 83.5 23.534 39.115 40.7 80.782 51.5 125z" opacity=".996"/><path fill="#000001" d="M887.5 470.5c-10.8-44.218-27.966-85.885-51.5-125-20.636-30.346-44.469-58.18-71.5-83.5-38.936-35.82-83.603-62.487-134-80-53.69-20.123-108.69-24.79-165-14-40.016 9.005-78.016 23.338-114 43-42.901 25.545-80.401 57.712-112.5 96.5-27.472 33.585-48.805 70.918-64 112-16.956 43.113-23.956 87.779-21 134 7.898 71.391 33.232 135.724 76 193a608.67 608.67 0 0 0 67.5 70c-41.104-29.937-75.937-65.937-104.5-108-79.92-130.022-80.586-260.356-2-391 35.965-52.966 80.799-96.799 134.5-131.5 134.728-75.885 266.394-70.885 395 15 49.702 36.358 90.202 80.858 121.5 133.5 23.469 42.573 38.636 87.907 45.5 136z" opacity=".432"/><path d="M816.5 139.5c1.904.238 3.238 1.238 4 3-1.904-.238-3.238-1.238-4-3z" opacity=".004"/><path fill="#000001" d="M820.5 142.5c96.391 73.675 157.224 170.342 182.5 290 24.59 129.49 2.92 250.49-65 363-76.025 116.49-182.525 188.99-319.5 217.5-140.775 24.96-269.442-3.37-386-85-106.222-80.207-170.222-186.374-192-318.5 11.933 55.208 32.767 106.875 62.5 155C179.1 882.51 286.266 955.677 424.5 984c116.18 19.64 225.514.637 328-57C889.103 843.595 967.937 722.095 989 562.5c13.25-168.416-42.917-308.416-168.5-420z" opacity=".426"/><path fill="#C1344E" d="M682.5 281.5c.473 1.406 1.473 2.073 3 2 .667 0 1 .333 1 1 30.306 26.132 56.473 55.799 78.5 89 44.091 70.989 52.424 145.989 25 225-14.961 37.283-35.961 70.616-63 100-32.169 36.189-70.002 64.689-113.5 85.5-82.407 34.114-161.74 28.114-238-18-53.495-35.159-96.328-80.326-128.5-135.5-10.459-20.228-18.625-41.228-24.5-63-15.198-77.186.302-147.52 46.5-211 29.415-39.42 64.582-72.586 105.5-99.5 91.333-54 182.667-54 274 0 11.93 7.453 23.264 15.62 34 24.5z" opacity=".998"/><path d="M682.5 281.5c1.527-.073 2.527.594 3 2-1.527.073-2.527-.594-3-2z" opacity=".004"/><path fill="#000001" d="M686.5 284.5c43.046 31.208 78.213 69.542 105.5 115 39.782 74.119 45.448 150.786 17 230-14.726 35.146-34.726 66.813-60 95-24.558 27.247-52.058 50.747-82.5 70.5-76.32 45.405-156.32 53.072-240 23-35.146-14.726-66.813-34.726-95-60-30.748-27.721-56.581-59.221-77.5-94.5-16.814-29.941-27.314-61.941-31.5-96 5.875 21.772 14.041 42.772 24.5 63 32.172 55.174 75.005 100.341 128.5 135.5 76.26 46.114 155.593 52.114 238 18 43.498-20.811 81.331-49.311 113.5-85.5 27.039-29.384 48.039-62.717 63-100 27.424-79.011 19.091-154.011-25-225-22.027-33.201-48.194-62.868-78.5-89z" opacity=".419"/><path d="M298.5 817.5c1.527-.073 2.527.594 3 2-1.527.073-2.527-.594-3-2z" opacity=".004"/></svg>'''
    
    return web.Response(text=svg, content_type="image/svg+xml")
    
async def index(request):
    return web.Response(text=HTML, content_type='text/html')
    

if __name__ == "__main__":

    # Startup
    try:
        window_title(TITLE)
    except AttributeError as e:
        pass
    
    cstr("Starting ComfyUI Gallery ...").msg.print()
       
    # CLI Arguments
        
    parser = argparse.ArgumentParser(prog='comfyui_explorer.py')
    parser.add_argument("--no-browser", action="store_true", help="Do not launch system browser when the server launches.")
    parser.add_argument("--purge-cache", action="store_true", help="Delete the image gallery cache on startup.")
    parser.add_argument('--image-paths', type=split_paths)
    parser.add_argument('--comfyui-path', type=str, default=None, help="Path to ComfyUI root directory.")
    parser.add_argument('--comfyui-color-palette', type=str, default=None, help="Path to a ComfyUI colorPalette.js file")
    args = parser.parse_args()

    if args.no_browser:
        NO_BROWSER = True
    if args.image_paths:
        for _ in args.image_paths:
            IMAGE_PATHS.append(_)
    if args.purge_cache:
        PURGE_CACHE = True
    if args.comfyui_path:
        CP_FILE = os.path.join(args.comfyui_path, CP_FILE)
        new_image_paths = []
        for img_path in IMAGE_PATHS:
            if not os.path.isabs(img_path):
                new_image_paths.append(os.path.join(args.comfyui_path, img_path))
            else:
                new_image_paths.append(img_path)
        IMAGE_PATHS = new_image_paths
    else:
        CP_FILE = os.path.join(ROOT, CP_FILE)
        new_image_paths = []
        for img_path in IMAGE_PATHS:
            if not os.path.isabs(img_path):
                new_image_paths.append(os.path.join(ROOT, img_path))
            else:
                new_image_paths.append(img_path)
        IMAGE_PATHS = new_image_paths
    if args.comfyui_color_palette:
        CP_FILE = args.comfyui_color_palette
    
            
    # HANDLE TEMP PATH
    if os.path.exists(THUMBNAIL_DIRECTORY) and PURGE_CACHE:
        shutil.rmtree(THUMBNAIL_DIRECTORY)
    os.makedirs(THUMBNAIL_DIRECTORY, exist_ok=True)
    
    # Setup CSS Colors
    COLORS = get_color_palettes(CP_FILE)

    light_css_colors = ""
    for key, value in COLORS['light']['colors']['node_slot'].items():
        light_css_colors += f"\t\t\t--{key.lower().replace('_','-')}: {value};\n"
    for key, value in COLORS['light']['colors']['comfy_base'].items():
        light_css_colors += f"\t\t\t--{key.lower().replace('_','-')}: {value};\n"
    light_css_colors += f"\t\t\t--header-bg: {COLORS['light']['colors']['litegraph_base']['NODE_DEFAULT_BOXCOLOR']};\n"
    light_css_colors += f"\t\t\t--class-menu-bg: {COLORS['light']['colors']['litegraph_base']['WIDGET_BGCOLOR']};\n"
    light_css_colors += f"\t\t\t--class-info-bg: {COLORS['light']['colors']['litegraph_base']['NODE_DEFAULT_BGCOLOR']};\n"
    light_css_colors += f"\t\t\t--main-text-color: {COLORS['light']['colors']['litegraph_base']['NODE_SELECTED_TITLE_COLOR']};\n"
    light_css_colors += f"\t\t\t--text-color: {COLORS['light']['colors']['litegraph_base']['NODE_TEXT_COLOR']};\n"
    light_css_colors += f"\t\t\t--alt-text-color: {COLORS['light']['colors']['litegraph_base']['NODE_TITLE_COLOR']};\n"
    light_css_colors += f"\t\t\t--alt-2-text-color: {COLORS['light']['colors']['litegraph_base']['WIDGET_SECONDARY_TEXT_COLOR']};\n"
    light_css_colors += f"\t\t\t--shadow-color: {COLORS['light']['colors']['litegraph_base']['DEFAULT_SHADOW_COLOR']};\n"
    light_css_colors += f"\t\t\t--link-color: {COLORS['light']['colors']['litegraph_base']['LINK_COLOR']};\n"
    light_css_colors += f"\t\t\t--event-link-color: {COLORS['light']['colors']['litegraph_base']['EVENT_LINK_COLOR']};\n"
    light_css_colors += f"\t\t\t--connecting-link-color: {COLORS['light']['colors']['litegraph_base']['CONNECTING_LINK_COLOR']};\n"
    light_css_colors += f"\t\t\t--connecting-link-color: {COLORS['light']['colors']['litegraph_base']['CONNECTING_LINK_COLOR']};\n"


    dark_css_colors = ""
    for key, value in COLORS['dark']['colors']['node_slot'].items():
        dark_css_colors += f"\t\t\t--{key.lower().replace('_','-')}: {value};\n"
    for key, value in COLORS['dark']['colors']['comfy_base'].items():
        dark_css_colors += f"\t\t\t--{key.lower().replace('_','-')}: {value};\n"
    dark_css_colors += f"\t\t\t--header-bg: {COLORS['dark']['colors']['litegraph_base']['NODE_DEFAULT_BOXCOLOR']};\n"
    dark_css_colors += f"\t\t\t--class-menu-bg: {COLORS['dark']['colors']['litegraph_base']['WIDGET_BGCOLOR']};\n"
    dark_css_colors += f"\t\t\t--class-info-bg: {COLORS['dark']['colors']['litegraph_base']['NODE_DEFAULT_BGCOLOR']};\n"
    dark_css_colors += f"\t\t\t--main-text-color: {COLORS['dark']['colors']['litegraph_base']['NODE_SELECTED_TITLE_COLOR']};\n"
    dark_css_colors += f"\t\t\t--text-color: {COLORS['dark']['colors']['litegraph_base']['NODE_TEXT_COLOR']};\n"
    dark_css_colors += f"\t\t\t--alt-text-color: {COLORS['dark']['colors']['litegraph_base']['NODE_TITLE_COLOR']};\n"
    dark_css_colors += f"\t\t\t--alt-2-text-color: {COLORS['dark']['colors']['litegraph_base']['WIDGET_SECONDARY_TEXT_COLOR']};\n"
    dark_css_colors += f"\t\t\t--shadow-color: {COLORS['dark']['colors']['litegraph_base']['DEFAULT_SHADOW_COLOR']};\n"
    dark_css_colors += f"\t\t\t--link-color: {COLORS['dark']['colors']['litegraph_base']['LINK_COLOR']};\n"
    dark_css_colors += f"\t\t\t--event-link-color: {COLORS['dark']['colors']['litegraph_base']['EVENT_LINK_COLOR']};\n"
    dark_css_colors += f"\t\t\t--connecting-link-color: {COLORS['dark']['colors']['litegraph_base']['CONNECTING_LINK_COLOR']};\n"
        
    # Define the ComfyUI Dictionary Webpage
    HTML = '''
    <!DOCTYPE html>
    <html data-theme="light">
    <head>
        <title>ComfyGallery</title>
        <meta charset="UTF-8">
        <link rel="icon" type="image/svg+xml" href="http://''' + DOMAIN + ''':''' + str(PORT) + '''/favicon.svg">
        <style id="theme-styles">
        
            /* GLOBAL COLORS */
        
            [data-theme="dark"] {
                ''' + dark_css_colors + '''
                --trans-light: rgba(255,255,255,0.5);
                --trans-dark: rgba(0,0,0,0.5);
                --svg-invert: invert(100%);
                --svg-active: invert(68%) sepia(79%) saturate(727%) hue-rotate(338deg) brightness(101%) contrast(101%);
                --svg-cat-color: invert(27%) sepia(49%) saturate(3049%) hue-rotate(327deg) brightness(85%) contrast(85%);
                --svg-menu-color: invert(58%) sepia(86%) saturate(342%) hue-rotate(108deg) brightness(95%) contrast(81%);
                --content-text-color: rgba(0,0,0,0.75);
                --content-text-shadow: 2px 2px 2px rgba(0,0,0,0.15);
                --cat-color: #c1344e;
                --menu-color: #39bf90;
                --gen-title: #EFEFEF;
            }

            [data-theme="light"] {
                ''' + light_css_colors + '''
                --trans-light: rgba(255,255,255,0.25);
                --trans-dark: rgba(0,0,0,0.25);
                --svg-invert: none;
                --svg-active: invert(70%) sepia(92%) saturate(1082%) hue-rotate(3deg) brightness(107%) contrast(106%);
                --svg-cat-color: invert(59%) sepia(72%) saturate(2627%) hue-rotate(183deg) brightness(102%) contrast(92%);
                --svg-menu-color: invert(60%) sepia(35%) saturate(1025%) hue-rotate(175deg) brightness(84%) contrast(90%);
                --content-text-color: rgba(0,0,0,0.75);
                --content-text-shadow: 2px 2px 2px rgba(0,0,0,0.15);
                --cat-color: #42a5f5;
                --menu-color: #4693ce;
                --gen-title: #eee;
            }
            
            /* MAIN BODY */
            
            body {
                font-family: sans-serif;
                margin: 0;
                background-color: var(--header-bg);
                text-shadow: 1px 1px 0px var(--shadow-color);
            }
            
            a, a:link, a:visited { color: var(--clip); transition: color .2s ease; }
            a:hover { color: var(--main-text-color); }
            a:active: { color: var(--image); }
                    
            #class-list-container #class-list .header p a.logo-link {
                display: inline-block;
                text-decoration: none;
                color: inherit !important;
                transition: all .2s ease;
            }
            
            #class-list-container #class-list .header p a.logo-link:hover { transform: scale(1.05) !important; }
            
            footer {
                position: fixed;
                height: 30px;
                background-color: var(--tr-odd-bg-color);
                box-shadow: inset 0px -10px 40px var(--shadow-color);
                border-top: 2px solid var(--trans-light);
                padding-top: 5px;
                padding: 10px 20px;
                text-align: right;
                bottom: 0;
                right: 0;
                left: 0;
                transition: background-color .125s ease;
            }
            
            footer .footer-content { font-size: 15px; color: var(--main-text-color); }
            
            footer .footer-content .node-bullet {
                color: var(--cat-color);
                font-size: 16px;
                margin-left: -1px;
                margin-right: -1px;
                vertical-align: inherit;
            }
            
            footer .footer-links {
                display: inline-block;
                margin-right: 20px;
                line-height: 30px;
            }
            
            footer .footer-links .link {
                padding-right: 10px;
                border-right: 1px solid var(--trans-dark);
                margin-left: 10px;
            }

            h1 {
                text-align: center;
            }
            
            /* INPUT */
            
            button {
                padding: 5px;
                background-color: var(--gen-title);
                border-radius: 5px;
                border: 1px solid var(--bg-color);
                font-size: 15px;
                font-weight: bold;
                color: var(--content-text-color);
            }
            
            button:hover {
                border-color: var(--clip);
            }
            
            button.active {
                background-color: var(--conditioning);
                border-color: var(--border-color);
            }
            
            .relative-container {
                position: relative !important;
            }

            /* NODE CLASS LIST */

            #logo-container {
                position: relative;
                background-color: var(--bg-color);
                border-bottom: 3px solid var(--alt-text-color);
                box-shadow: 0px 0px 25px 11px rgba(0,0,0,0.25);
                z-index: 1000;
            }

            #logo-container .header {
                position: relative;
                padding: 0;
                margin: 0;
                height: 80px;
            }

            #logo-container .header p {
                margin: 0;
                position: absolute;
                top: 40%;
                left: 60px;
                transform: translateY(-50%);
                text-transform: uppercase;
                letter-spacing: 2px;
                font-weight: bold;
                font-size: 28px;
                color: var(--main-text-color);
                text-shadow: 2px 2px 0 var(--shadow-color);
                line-height: 15px;
                text-dectoration: none;
            }
            
            #logo-container .header p a,
            #logo-container .header p a:link,
            #logo-container .header p a:active,
            #logo-container .header p a:visited {
                color: var(--main-text-color);
                text-decoration: none !important;
                transition: all .2s ease;
            }
            
            #logo-container .header p a:hover {
                color: var(--clip);
            }
            
            #logo-container .header p a .comfyui {
                font-size: small;
                padding-bottom: 4px;
                display: inline-block;
                padding-left: 2px;
            }
            
            /* CLASS INFO */

            #class-info {
                width: 100%;
                color: var(--main-text-color);
                overflow: auto;
                transition: background-color .125s ease;
                padding-bottom: 0;
            }
            
            #class-info .content {
                padding: 10px;
                padding-bottom: 0;
                margin: 10px 50px 0 50px
            }
            
            #class-info .content .title {
                margin: 10px 0;
                font-size: 28px;
                color: var(--gen-title);
                text-shadow: 2px 2px 0 var(--shadow-color);
                border-bottom: 1px solid var(--trans-dark);
                padding-bottom: 4px;
            }
            
            #class-info .content .title .subtitle { font-weight: 100; color: var(--content-text-color); }
            #class-info .content h2,
            #class-info .content h3 { margin: 10px 5px; color: var(--main-text-color); }
            #class-info .content h4 { color: var(--main-text-color); }
            #class-info .content p { max-width: 1280px; margin: 4px; }
            #class-info .content p.description { margin: 15px 5px; font-size: 16px; }
            #class-info .content .class-info-address { font-size: 18px; margn-left: 5px; }
            li.active { background-color: var(--shadow-color); color: var(--text-color) !important; }
            
            .node-bullet {
                color: var(--cat-color);
                font-size: 30px;
                vertical-align: top;
                display: inline-block;
                margin-left: -2px;
                margin-right: -2px;
                padding-top: 2px;
            }
            
            /* General Content */
            
            .gen-container {
                background-color: var(--bg-color);
                box-shadow: 0 0 0 5px var(--comfy-menu-bg);
                margin-top: 20px;
                margin-bottom: 25px;
                margin-left: 0;
                margin-right: 0;
                border-radius: 8px;
            }
            
            .gen-menu {
                background-color: var(--class-info-bg);
                border-bottom: 1px solid rgba(0,0,0,0.25);
                border-top: 1px solid rgba(255,255,255,0.25);
                padding: 5px 10px;
                box-shadow: inset 0 0 20px var(--shadow-color);
                color: var(--alt-2-text-color);
            }
                    
            .gen-container h3 {
                padding: 10px !important;
                margin: 0 !important;
                border-bottom: 1px solid var(--shadow-color);
                border-top: 3px solid rgba(255,255,255,0.2);
                background-color: var(--cat-color);
                border-top-right-radius: 8px;
                border-top-left-radius: 8px;
                color: var(--gen-title) !important;
                text-shadow: 2px 2px 0 var(--shadow-color);
            }
            
            .gen-container .gen-content {
                padding: 0 !important;
                margin: 0 !important;
                border-top: 1px solid var(--trans-light);
                font-size: 16px;
            }
            
            .gen-container .gen-content .gen-content-subcontainer { padding: 5px 15px; }
            .gen-container .gen-content .gen-content-subcontainer h4 { margin-top: 5px; }
            
            .gen-scroll {
                overflow: auto;
                max-height: fit-content;
                height: 500px;
                resize: vertical;
                border-top: 1px solid var(--trans-light);
            }
            
            .code {
                display: inline-block;
                width: 95%;
                color: inherit;
                padding: 10px;
                border-radius: 3px;
                margin-bottom: 5px;
                opacity: 0.75;
                background-color: rgba(255,255,255,0.2);
                border-bottom: 1px solid rgba(0,0,0,0.15);
                border-right: 1px solid rgba(0,0,0,0.15);
                border-left: 1px solid var(--trans-light);
                border-top: 1px solid var(--trans-light);
                box-shadow: inset -20px -5px 60px var(--shadow-color);
            }
            
            .font-normal {
                font-weight: normal !important;
                text-decoration: none !important;
            }
            
            .font-normal a {
                font-weight: normal;
                text-decoration: none !important;
            }
            
            .code h5 { margin: 5px; var(--alt-2-text-color); }
            
            .code ol {
                margin: 0;
                padding-left: 20px;
                counter-reset: item;
                list-style-type: none;
                counter-reset: combo-id -1;
            }
            
            .code ol li { margin: 0 5px 0 5px; counter-increment: combo-id; }
            
            .code ol li:before {
                display: inline-block;
                width: 45px;
                margin-right: 10px;
                text-align: right;
                content: " [" counter(combo-id) "] ";
            }
                 
            .text-block {
                max-width: 70ch;
                line-height: 1.5;
                text-align: justify;
                text-justify: inter-word;
                font-size: 16px;
                margin-bottom: 25px !important; 
            }
                    
            /* Image Gallery */
            
            .image-gallery {
                display: flex;
                margin: 10px;
                flex-wrap: wrap;
                justify-content: flex-start;
                align-items: flex-start;
                gap: 10px;
                justify-content: space-evenly;
            }

            .image-container {
                flex: 0 0 calc(25% - 10px);
                max-width: 400px;
                height: auto;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                background-color: var(--trans-dark);
                border: 1px solid var(--border-color);
                border-radius: 8px;
                overflow:hidden;
            }
            
            .image-container a img { max-width: 400px; }
            #image-gallery { margin-bottom: 20px !important; padding-bottom: 0 !important; }
            .gen-scroll.image-gallery-scroll,
            .image-gallery-scroll { height: 300px; max-height: fit-content }
            #image-gallery.content .gen-container .gen-content-subcontainer.gen-scroll { padding-top: 80px; height: 75vh; }
            
            .gen-gallery-container {
                display: flex;
                flex-wrap: wrap;
                justify-content: space-evenly;
                gap: 10px;
            }

            #button-container {
                position: absolute;
                top: 70px;
                left: 0;
                right: 0;
                padding: 8px;
                background-color: rgba(65,65,65,0.5);
                z-index: 9999;
                line-height: 50px;
            }
            
            #button-container button {
                padding: 5px;
                background-color: var(--gen-title);
                border-radius: 5px;
                border: 1px solid var(--bg-color);
                font-size: 15px;
                font-weight: bold;
                color: var(--content-text-color);
            }
            
            #button-container button:hover { border-color: var(--clip); }
            
            #home-button {
                background-color: var(--cat-color) !important;
                border: 1px solid var(--clip-vision);
                border-radius: 5px;
                color: var(--gen-title);
            }
            
            #home-button:disabled, #button-container button:disabled {
                background-color: var(--class-menu-bg) !important;
                border: 1px solid var(--border-color) !important;
                color: var(--border-color);
                opacity: 0.7;
            }

            .directory {
                display: flex;
                padding: 10px;
                flex: 0 0 calc(12%);
                align-items: center;
                background-color: var(--trans-dark);
                border: 1px solid transparent;
                border-radius: 8px;
                font-weight: bold;
                word-wrap: break-word;
                cursor: pointer;
                transition: all .2s ease;
            }
            
            .directory:before {
                display: inline-block;
                content: '📂';
                margin-right: 5px;
                margin-bottom: 5px;
            }
            
            .directory:hover { box-shadow: inset 0 0 20px var(--class-info-bg); color: var(--clip); }

            .gallery-image-container {
                flex: 0 0 calc(25% - 10px);
                max-width: 200px;
                height: fit-content !important;
                margin: 5px;
                padding: 5px;
                background-color: var(--class-info-bg);
                border-radius: 5px;
                border: 1px solid var(--border-color);
                box-shadow: inset 0px 0px 10px var(--bg-color);
                transition: transform .2s ease;
            }
            
            .gallery-image-container:hover { transform: scale(1.05); box-shadow: 0 0 5px var(--clip); }
            
            .gen-image-link { max-width: 200px; cursor: pointer; }
                    
            .gallery-image-container a img { 
                max-width: 200px; 
                cursor: pointer; 
                border: 1px solid var(--border-color);
                border-radius: 4px;
            }
            
            .gen-gallery-image-title {
                font-size: 12px;
                letter-spacing: 1px;
                text-align: center; 
                padding-top: 5px;
                padding-bottom: 5px;
                overflow:hidden;
                word-wrap: break-word;
            }
            
            #category-dropdown { display: inline-block !important; }
            
            #category-dropdown select {
                background-color: var(--cat-color);
                font-weight: bold;
                font-size: 28px;
                border: 1px solid var(--border-color);
                border-radius: 4px;
                padding: 5px;
                color: var(--gen-title);
                font-size: 28px;
                text-shadow: 2px 2px 2px var(--shadow-color);
                box-shadow: 0 0 0 2px var(--fg-color);
            }
            
            .gen-gal-sep {
                display: block !important;
                margin-bottom: 20px;
                width: 100%;
                border-top: 1px solid rgba(255,255,255,0.2);
                border-bottom: 2px solid rgba(0,0,0,0.3);
                box-shadow: 0 2px 2px rgba(0,0,0,0.2);
            }
            
            .image-gallery-search {
                float: right;
                margin-right: 10px;
            }
            
            .image-gallery-search input {
                padding: 5px;
                border-radius: 6px;
                font-size: 17px;
                color: var(--main-text-color);
                background-color: var(--bg-color);
                border: 2px solid var(--fg-color);
                width: 300px;
                min-width: 100px;
                opacity: 0.5;
                transition: all .2s ease;
            }
            
            .image-gallery-search input:focus {
                border-color: var(--clip);
                opacity:1;
            }
                        
            /* NODE SEARCH */
            
            .search-container { position: relative; }
            
            #search {
                display: inline-block;
                width: 100%;
                background-color: var(--tr-odd-bg-color);
                border-top: 1px solid var(--trans-dark);
                border-bottom: 1px solid var(--trans-dark);
                border-left: none;
                border-right: none;
                padding: 8px 44px;
                box-shadow: inset -2px -2px 4px var(--shadow-color);
                color: var(--text-color);
                font-size: 18px;
                box-shadow: inset -2px -0px 8px var(--shadow-color);
                transition: background-color, color  .125s ease;
            }
            
            .search-icon {
                position: absolute;
                top: 50%;
                left: 15px;
                transform: translateY(-55%);
            }
            
            .search-icon:before{ content: '🔎︎'; color: var(--clip); }
            
            /* IMAGE MODAL */
            
            #image-modal {
                transition: all .3s ease !important;
                display: none;
                opacity: 0;
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                z-index: 10000;
                background-color: rgba(5,5,5,0.9);
                box-shadow: inset 0 0 500px 250px rgb(0,0,0);
            }
            
            #image-modal.view {
                transition: all .3s ease;
                display: block;
                opacity: 1;
            }
            
            .image-modal-view {
                position: fixed;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                max-width: 90%;
                max-height: 90%;
                padding: 20px;
                background-color: var(--class-menu-bg);
                box-shadow: 0 0 100px rgba(0,0,0,0.5);
                border-radius: 10px;
                z-index: 11000;
            }
            
            #modal-image {
                max-width: 90vw;
                max-height: 80vh;
            }
            
            .image-modal-controls {
                padding-top: 0;
                padding-bottom: 20px;
            }
            
            .image-modal-image {
                position: relative;
            }
            
            #matched-object {
                display: none;
                position: absolute;
                bottom: 0;
                left: 0;
                right: 0;
                overflow: hidden;
                max-height: fit-content;
                height: 0;
                border-top: 2px solid transparent;
                background-color: transparent;
                padding: 0;
                transition: all .4s ease;
            }
            
            #matched-object pre {
                padding: 5px;
                color: var(--main-text-color);
            }
            
            .image-modal-image:hover #matched-object,
            .image-modal-image:focus #matched-object {
                overflow: auto;
                height: 50%;
                background-color: rgba(0,0,0,0.85);
                border-top: 2px solid var(--border-color);
            }
            
            .matched-header {
                height: 20px
                line-height: 20px;
                background-color: var(--class-menu-bg);
                color: var(--main-text-color);
                padding: 5px 10px;
                font-weight: bold;
                border-bottom: 1px solid rgba(0,0,0,0.8);
            }
            
            .del-confirm-block {
                display: none;
                opacity: 1;
                color: var(--clip);
                margin-left: 10px;
                margin-right: 20px;
            }
            
            #del-image-btn {
                background-color: var(--border-color);
                border-color: var(--error-text);
                color: var(--error-text);
                cursor: pointer;
            }
            
            #copy-workflow-btn {
                background-color: var(--image);
                color: var(--fg-color);
            }
            
            #copy-workflow-btn {
                float: right;
            }
            
            /* THEME PICKER */
            
            #theme-picker {
                position: absolute;
                right: 70px;
                top: 23px;
                padding: 5px;
                background-color: var(--bg-color);
                border-radius: 100%;
                box-shadow: 0 0 6px var(--shadow-color);
                width: 24px;
                height: 24px;
                text-align: center;
                line-height: 24px;
                font-size: 32px;
                opacity: 0.25;
                transition: opacity .2s ease;
                z-index: 10000
            }
            
            #theme-picker:hover { opacity: 1.0; }
            
            #theme-picker div {
                user-select: none;
                -moz-user-select: none;
                -khtml-user-select: none;
                -webkit-user-select: none;
                -o-user-select: none;
                cursor: pointer;
                color: var(--main-text-color);
            }
            
            #theme-picker div.active-dark { text-shadow: 0 0 8px rbg(255,255,255); transition: text-shadow .2s ease; }
            #theme-picker div.active-light { text-shadow: 0 0 8px rbg(0,0,0); transition: text-shadow .2s ease; }
            #theme-picker div.active-light:hover,
            #theme-picker div.active-dark:hover { text-shadow: 0 0 10px var(--clip) !important; color: var(--clip); }
            
            /* SOURCE CODE */
            
            .gen-container h3 { position: relative; }
            
            .gen-container h3 .clipboard-act {
                position: absolute;
                right: 20px;
                top: 10px;
                background-image: url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-clipboard-text" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M9 5h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2h-2"/><path d="M9 3m0 2a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v0a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2z"/><path d="M9 12h6"/><path d="M9 16h6"/></svg>');

                background-repeat: no-repeat;
                background-position: center; 
                filter: var(--svg-invert);
                width: 20px;
                height: 20px;
                opacity: 0.75;
                cursor: pointer;
                transition: all .2s ease;
            }
            
            .gen-container h3 .clipboard-act:hover { opacity: 1.0; }
            
            .gen-container h3 .clipboard-act.active {
                background-image: url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-clipboard-check" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M9 5h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2h-2" /><path d="M9 3m0 2a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v0a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2z" /><path d="M9 14l2 2l4 -4" /></svg>');
                filter: var(--svg-active);
                opacity: 1.0;
            }
            
            /* ANIMATIONS */
            
            @keyframes pulse {
                0%   { opacity:1; }
                50%  { opacity:0.25; }
                100% { opacity:1; }
            }
            @-o-keyframes pulse{
                0%   { opacity:1; }
                50%  { opacity:0.25; }
                100% { opacity:1; }
            }
            @-moz-keyframes pulse{
                0%   { opacity:1; }
                50%  { opacity:0.25; }
                100% { opacity:1; }
            }
            @-webkit-keyframes pulse{
                0%   { opacity:1; }
                50%  { opacity:0.25; }
                100% { opacity:1; }
            }
            .loading {
               -webkit-animation: pulse 1s infinite;
               -moz-animation: pulse 1s infinite;
               -o-animation: pulse 1s infinite;
                animation: pulse 1s infinite;
            }

        </style>
    </head>
    <body>

        <!-- TEMPLATE -->
        <div class="page-content">
            <div id="logo-container">
                <div class="header">
                        <p>
                            <a id="logo-link" class="logo-link" href="#home" title="Go to homepage"><span class="comfyui">ComfyUI</span><br>
                            C<span class="node-bullet">&#10687;</span>mfy<span style="font-weight:normal;">Gallery</span></a>
                        </p>
                </div>
            </div>
            <div id="class-info">
                <h3><i class="loading">Loading...</i></h3>
            </div>
            <div id="theme-picker">
                <div id="theme-toggle" class="active-light">&#9681;</div>
            </div>
        </div>
        
        <footer>
            <div class="footer-content">
                <div class="footer-links">
                    <span class="link"><a href="https://github.com/comfyanonymous/ComfyUI" title="ComfyUI Github">ComfyUI Github</a></span>
                </div>
                &copy;2023 <strong>C<span class="node-bullet">&#10687;</span>MFY</span></strong>GALLERY [Alpha]. Licensed under <a href="http://www.gnu.org/licenses/gpl.html" target="_blank">GNU GPL-3</a>
            </div>
        </footer>
        
        <div id="image-modal" onclick="fadeOut(this, 0.1);">
            <div class="image-modal-view" onclick="event.stopPropagation();">
                <div class="image-modal-controls">
                    <button id="del-image-btn">Delete Image</button>
                    <div id="del-confirm-modal" class="del-confirm-block" style="display:none;">
                        Are you sure? 
                        <button id="del-confirm">Yes</button>
                        <button id="del-abort">No</button>
                    </div>
                    <button id="copy-workflow-btn">Copy Workflow</button>
                </div>
                <div class="image-modal-image">
                    <a id="modal-link" href="#" target="_blank"><img id="modal-image" src="#" alt="#"></a>
                    <div id="matched-object" style="display:none;"></div>
                </div>
            </div>
        </div>

        <script>
            const address = 'http://''' + DOMAIN + ''':''' + str(PORT) + '''',
                classListContainer = document.getElementById('class-list-container'),
                classList = document.getElementById('class-list'),
                classInfo = document.getElementById('class-info');
                imageModal = document.getElementById("image-modal"),
                modalImage = document.getElementById("modal-image"),
                modalLink = document.getElementById("modal-link"),
                delContainer = document.getElementById("del-confirm-modal"),
                deleteButton = document.getElementById("del-image-btn"),
                confirmButton = document.getElementById("del-confirm"),
                abortButton = document.getElementById("del-abort"),
                copyWorkflowBtn = document.getElementById("copy-workflow-btn"),
                matchedContent = document.getElementById("matched-object"),
                logoLink = document.getElementById("logo-link")
                
            let pathHistory = [],
                selectedCategory,
                selectedPath,
                previousPath,
                categoryDropdown,
                galleryContainer,
                homeButton,
                backButton,
                galSearchInput,
                matched = [];

            
            // OPACITY ANIMATIONS
            
            function fadeIn(element, speed=0.01, display='block') {
                const duration = speed * 1000;
                const startTime = performance.now();
                const initialOpacity = parseFloat(getComputedStyle(element).opacity);
                let opacity = initialOpacity;

                element.style.display = display;

                function animate(currentTime) {
                    const elapsedTime = currentTime - startTime;
                    const progress = Math.min(elapsedTime / duration, 1);

                    opacity = initialOpacity + progress * (1 - initialOpacity);
                    element.style.opacity = opacity;

                    if (progress < 1) {
                        requestAnimationFrame(animate);
                    }
                }

                requestAnimationFrame(animate);
            }

            function fadeOut(element, speed = 0.01) {
                const duration = speed * 1000; 
                const startTime = performance.now();
                let opacity = 1;

                function animate(currentTime) {
                    const elapsedTime = currentTime - startTime;
                    const progress = Math.min(elapsedTime / duration, 1);

                    opacity = 1 - progress;
                    element.style.opacity = opacity;

                    if (progress < 1) {
                        requestAnimationFrame(animate);
                    } else {
                        element.style.display = 'none'; 
                    }
                }

                requestAnimationFrame(animate);
            }                
            
            // THEME PICKER
            
            function setTheme(theme) {
                const themeStyles = document.getElementById('theme-styles');
              
                if (theme === 'dark') {
                    document.documentElement.setAttribute('data-theme', 'dark');
                    themeStyles.className = 'active-dark';
                } else if (theme === 'light') {
                    document.documentElement.setAttribute('data-theme', 'light');
                    themeStyles.className = 'active-light';
                }
              
                localStorage.setItem('theme', theme);
            }

            const themeToggle = document.getElementById('theme-toggle');
            themeToggle.addEventListener('click', function() {
                const currentTheme = document.documentElement.getAttribute('data-theme');
                const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
                setTheme(newTheme);
                themeToggle.className = newTheme === 'dark' ? 'active-dark' : 'active-light';
                themeToggle.innerHTML = newTheme === 'dark' ? '&#9681;' : '&#9681;';
            });

            const savedTheme = localStorage.getItem('theme');
            if (savedTheme) {
                setTheme(savedTheme);
            }
            
            function copySourceCode() {
                var copyButton = document.getElementById('copy-source');
                var sourceCodeBlock = document.querySelector('.source-code-block');

                copyButton.classList.add('active');
                setTimeout(function() {
                    copyButton.classList.remove('active');
                }, 1200);

                var plainSourceCode = sourceCodeBlock.innerText;

                var tempTextarea = document.createElement('textarea');
                tempTextarea.value = plainSourceCode;
                document.body.appendChild(tempTextarea);
                tempTextarea.select();
                document.execCommand('copy');
                document.body.removeChild(tempTextarea);
            }
           
           
            // GEN IMAGE GALLERY

            function generateImageGallery(categories) {
                classInfoDiv = document.getElementById('class-info');
                pathHistory = [];

                var html = `
                    <div id="image-gallery" class="content">
                        <div class="gen-container relative-container">
                            <h3 class="title"><div id="category-dropdown"></div> Gallery</h3>
                            <div class="gen-content-subcontainer gen-scroll">
                                <div id="button-container">
                                    <button id="home-button" disabled>Home</button>
                                    <button id="back-button" disabled>Back</button>
                                    <div class="image-gallery-search"><input id="image-gallery-search-input" type="text" placeholder="Search by filename or workflow" /></div>
                                </div>
                                <div id="gen-gallery-container" class="gen-gallery-container"></div>
                            </div>
                        </div>
                    </div>
                `;

                classInfoDiv.innerHTML = html;
                
                categoryDropdown = document.getElementById('category-dropdown');
                galleryContainer = document.getElementById('gen-gallery-container');

                var dropdownHtml = '<select id="category-select">';
                for (var i = 0; i < categories.length; i++) {
                    if (i === 0) {
                        dropdownHtml += '<option value="' + categories[i] + '" selected>' + categories[i] + '</option>';
                    } else {
                        dropdownHtml += '<option value="' + categories[i] + '">' + categories[i] + '</option>';
                    }
                }
                dropdownHtml += '</select>';
                categoryDropdown.innerHTML = dropdownHtml;

                categorySelect = document.getElementById('category-select');
                selectedCategory = categories[0];

                categorySelect.addEventListener('change', function () {
                    selectedCategory = categorySelect.value;
                    loadImageGallery(selectedCategory, '/');
                });

                homeButton = document.getElementById('home-button');
                backButton = document.getElementById('back-button');
                galSearchInput = document.getElementById('image-gallery-search-input');

                homeButton.addEventListener('click', handleHomeButtonClick);
                backButton.addEventListener('click', handleBackButtonClick);
                galSearchInput.addEventListener('keydown', function (event) {
                    if (event.key === 'Enter') {
                        var searchValue = galSearchInput.value.trim();
                        if (searchValue !== '') {
                            searchImages(searchValue);
                        }
                    }
                });
    
                updateButtonStates(true);

                loadImageGallery(selectedCategory, '/');
            }
            
            function searchImages(query) {
                var url = '/search_images?query=' + encodeURIComponent(query);
                galleryContainer = document.getElementById('gen-gallery-container');
                
                galleryContainer.innerHTML = '<i class="loading">Searching images...</i>';
                
                fetch(url)
                    .then(response => response.json())
                    .then(data => {
                        console.log(data);
                        displaySearchResults(data);
                    })
                    .catch(error => console.error(error));
            }
            
            function displaySearchResults(searchResults) {
                var images = searchResults.images,
                    imagesHtml = '';
                galleryContainer = document.getElementById('gen-gallery-container');
                matched = []
                backButton.disabled = true;

                if ( images.length > 0 ) {
                    for (var i = 0; i < images.length; i++) {
                        var match = ( images[i].matched ? images[i].matched : 'none' );
                        matched.push(match);
                        imagesHtml += '<div id="image-container-' + i + '" class="gallery-image-container">';
                        imagesHtml += '<img id="gen-image-' + i + '" class="gen-image-link" data-category="' + images[i].category + '" data-path="' + images[i].path + '" data-matched="' + i + '" src="/get_image?category=' + images[i].category + '&path=' + images[i].path + '" alt="' + images[i].path.split("/").pop() + '">';
                        imagesHtml += '<div class="gen-gallery-image-title" title="' + images[i].path.split("/").pop() + '">' + images[i].path.split("/").pop() + '</div>';
                        imagesHtml += '</div>';
                    }
                } else {
                    imagesHtml += '<i>No matches found.</i>';
                }

                galleryContainer.innerHTML = imagesHtml;

                var imgElements = galleryContainer.getElementsByClassName('gen-image-link');
                for (var i = 0; i < imgElements.length; i++) {
                    imgElements[i].addEventListener('click', handleImageModal);
                }
                
                selectedPath = "/nd-search-results";

                updateButtonStates();
            }
                        
            function deleteImage(category, path) {
                var url = '/delete_image?category=' + encodeURIComponent(category) + '&path=' + encodeURIComponent(path);
                return fetch(url)
                    .then(response => {
                        if (response.ok) {
                            return response.json();
                        }
                        throw new Error("Delete request failed");
                    })
                    .then(data => {
                        if (data.success === true) {
                            return true;
                        }
                        return false;
                    })
                    .catch(error => {
                        console.error(error);
                        return false;
                    });
            }
            
            function handleDirectoryClick(e) {
                previousPath = selectedPath;
                selectedPath = e.target.dataset.path;
                console.log("Directory path: " + selectedPath);
                pathHistory.push(previousPath);
                loadImageGallery(selectedCategory, selectedPath, previousPath);
            }

            function handleHomeButtonClick() {
                if (!homeButton.disabled) {
                    pathHistory.push(selectedPath);
                    selectedPath = '/';
                    loadImageGallery(selectedCategory, selectedPath);
                }
            }

            function handleBackButtonClick() {
                if (pathHistory.length > 0) {
                    selectedPath = pathHistory.pop();
                    console.log("Loading back path: " + selectedPath);
                    loadImageGallery(selectedCategory, selectedPath);
                }
            }
                        
            function handleImageModal(e) {
                e.preventDefault();
                var category = e.target.dataset.category,
                    path = e.target.dataset.path,
                    matchedId = e.target.dataset.matched,
                    directory_path = path.includes("/") ? path.split("/").slice(0, -1).join("/") : "/",
                    originTarget,
                    originImage;
                    if (matchedId) {
                        originTarget = document.getElementById('image-container-'+matchedId);
                        originImage = document.getElementById('gen-image-' + matchedId);
                    }

                deleteButton.removeEventListener("click", deleteButtonClickHandler);
                confirmButton.removeEventListener("click", confirmButtonClickHandler);
                abortButton.removeEventListener("click", abortButtonClickHandler);
                
                modalImage.src = category + '/' + path;
                modalLink.href = category + '/' + path;
                imageModal.style.display = 'block';
                if (matchedId) {
                    console.log("Displaying matched content...");
                    matchedContent = document.getElementById('matched-object');
                    matchedContent.innerHTML = '<div class="matched-header">Matched Workflow Content</div><pre>' + JSON.stringify(matched[matchedId], null, 4) + '</pre>';
                    matchedContent.style.display = 'block';
                }

                function confirmButtonClickHandler(e) {
                    deleteImage(category, path)
                        .then(success => {
                            if (success) {
                                fadeOut(imageModal, 0.1);
                                fadeOut(e.target.parentElement, 0.1);
                                if (!matchedId) {
                                    loadImageGallery(category, directory_path);
                                } else {
                                    matchedContent.innerHTML = '';
                                    matchedContent.style.display = 'none';
                                    originImage.removeEventListener("click", handleImageModal);
                                    originImage.style.cursor = 'default';
                                    originTarget.style.opacity = 0.5;
                                    originTarget.style.filter = 'grayscale(1)';
                                }
                                e.stopPropagation();
                                e.preventDefault();
                            } else {
                                console.error("Failed to delete image.");
                            }
                        });
                }

                function abortButtonClickHandler(e) {
                    var delContainer = document.getElementById("del-confirm-modal");
                    fadeOut(delContainer, 0.1);
                }

                function deleteButtonClickHandler(e) {
                    var delContainer = document.getElementById("del-confirm-modal");
                    var abortButton = document.getElementById("del-abort");
                    fadeIn(delContainer, 0.2, "inline-block");
                }
                
                function copyWorkflow(e) {
                    var url = '/get_workflow?category=' + encodeURIComponent(category) + '&path=' + encodeURIComponent(path);
                    return fetch(url)
                        .then(response => {
                            if (response.ok) {
                                return response.json();
                            }
                            throw new Error("Could not retrieve workflow from: "+path);
                        })
                        .then(data => {
                            let dataString = JSON.stringify(data);
                            e.target.classList.add('active');
                            setTimeout(function() {
                                e.target.classList.remove('active');
                            }, 1200);
                            let selBox = document.createElement('textarea');
                            selBox.style.position = 'fixed';
                            selBox.style.left = '0';
                            selBox.style.top = '0';
                            selBox.style.opacity = '0';
                            selBox.value = dataString;
                            document.body.appendChild(selBox);
                            selBox.focus();
                            selBox.select();
                            document.execCommand('copy');
                            document.body.removeChild(selBox);
                            console.log("Workflow for " + path + " copied.")
                        })
                        .catch(error => {
                            console.log("Unable to copy workflow for ' + path + '.")
                            console.error(error);
                            return false;
                        });  
                }

                deleteButton.addEventListener("click", deleteButtonClickHandler);
                confirmButton.addEventListener("click", confirmButtonClickHandler);
                abortButton.addEventListener("click", abortButtonClickHandler);
                copyWorkflowBtn.addEventListener("click", copyWorkflow);

                fadeIn(imageModal, 0.2);
            }
            
            function loadImageGallery(category, path, previousPath) {
                var url = '/get_paths?category=' + encodeURIComponent(category) + '&path=' + encodeURIComponent(path);

                fetch(url)
                    .then(response => response.json())
                    .then(data => {
                        var json = JSON.parse(data);

                        var directories = json.directories;
                        var images = json.images;
                        selectedPath = path;

                        var directoriesHtml = '';
                        for (var i = 0; i < directories.length; i++) {
                            directoriesHtml += '<div class="directory" data-category="' + category + '" data-path="' + directories[i] + '">' + directories[i] + '</div>';
                            if (i == directories.length - 1)
                                directoriesHtml += '<div class="gen-gal-sep"></div>';
                        }

                        var imagesHtml = '';
                        for (var i = 0; i < images.length; i++) {
                            imagesHtml += '<div id="image-container-' + i + '" class="gallery-image-container">';
                            imagesHtml += '<img id="gen-image-' + i + '" class="gen-image-link" data-category="' + category + '" data-path="' + images[i] + '" src="/get_image?category=' + category + '&path=' + images[i] + '" alt="' + images[i].split("/").pop() + '"><div class="gen-gallery-image-title" title="' + images[i].split("/").pop() + '">' + images[i].split("/").pop() + '</div>';
                            imagesHtml += '</div>';
                        }

                        galleryContainer.innerHTML = directoriesHtml + imagesHtml;

                        var directoryElements = galleryContainer.getElementsByClassName('directory');
                        for (var i = 0; i < directoryElements.length; i++) {
                            directoryElements[i].addEventListener('click', handleDirectoryClick);
                        }

                        var imgElements = galleryContainer.getElementsByClassName('gen-image-link');
                        for (var i = 0; i < imgElements.length; i++) {
                            imgElements[i].addEventListener('click', handleImageModal);
                        }

                        updateButtonStates();
                    })
                    .catch(error => console.error(error));
            }

            function updateButtonStates(disable=false) {
                if (selectedPath === '/' || disable === true) {
                    if ( homeButton.disabled !== true )
                        homeButton.disabled = true;
                    if ( backButton.disabled !== true )
                        backButton.disabled = true;
                } else {
                    homeButton.disabled = false;
                    backButton.disabled = false;
                }
            }

            // PAGE NAVIGATION

            function pageNavigation() {
                let windowHash = window.location.hash;
                let hashName = decodeURIComponent(windowHash.replace('#', ''));

                if (hashName == 'home') {
                    var categories = [''' + ",".join('"' + os.path.basename(path) + '"' for path in IMAGE_PATHS) + '''];
                    generateImageGallery(categories);
                    return null;
                }

            }

            window.addEventListener('hashchange', pageNavigation);
            logoLink.addEventListener('click', pageNavigation)

            if ( window.location.hash === '' || window.location.hash ==='#home' ) {
                var categories = [''' + ",".join('"' + os.path.basename(path) + '"' for path in IMAGE_PATHS) + '''];
                generateImageGallery(categories);
            }

        </script>
    </body>
    </html>
    '''

    # HTTP SERVER

    cstr(f"Starting Server with Domain: {DOMAIN},  Port:{PORT}").msg.print()
    middlewares = [create_cors_middleware('*'), ] 
    app = web.Application(client_max_size=20971520, middlewares=middlewares)
    app.router.add_get('/get_image', get_image)
    app.router.add_get('/search_images', search_images)
    app.router.add_get('/get_paths', get_directory)
    app.router.add_get('/get_workflow', get_workflow)
    app.router.add_get('/delete_image', delete_image)
    app.router.add_get('/favicon.svg', get_fav_icon)
    for image_res in IMAGE_PATHS:
        resource = web.StaticResource('/' + os.path.basename(image_res), image_res)
        app.router.register_resource(resource)
    app.router.add_get('/', index)

    if not NO_BROWSER:
        webbrowser.open_new_tab(f'http://{DOMAIN}:{PORT}')
    web.run_app(app, host=DOMAIN, port=int(PORT))
