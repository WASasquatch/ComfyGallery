# ComfyGallery
A light-weight browser based gallery for ComfyUI

<img width="400" src="https://i.postimg.cc/2y3MgtfH/Screenshot-2023-08-09-105327.png">

## Launch Arguments
 - `--no-browser`: Do not launch system browser when the server launches.
 - `--purge-cache`: Delete the image gallery cache on startup.
 - `--image-paths`: A list of additional image paths for ComfyGallery to manage.
 - `--comfyui-path`: Path to ComfyUI root directory.
 - `--comfyui-color-palette`: Path to a ComfyUI colorPalette.js file

## Installation

Clone the project to a location of your choosing. You can move comfy_gallery.py to the root of your ComfyUI if you'd like, or run it from where it's at. 

**Exmaple Launch Batch File** (from ComfyUI Portable):
```batch
.\python_embeded\python.exe -s ComfyUI\comfy_gallery.py

pause
```

## Notes

If you're running ComfyGallery from outside ComfyUI you'll need to provide the ComfyUI root directory to it with the `--comfyui-path` launch argument. 
