# ComfyGallery
A light-weight browser based gallery for ComfyUI

## Launch Arguments
 - `--no-browser`: Do not launch system browser when the server launches.
 - `--purge-cache`: Delete the image gallery cache on startup.
 - `--image-paths`: A list of additional image paths for ComfyGallery to manage.
 - `--comfyui-path`: Path to ComfyUI root directory.
 - `--comfyui-color-palette`: Path to a ComfyUI colorPalette.js file

## Installation

Clone the project to a location of your choosing. You can move comfy_gallery.py to the root of your ComfyUI if you'd like, or run it from where it's at. 

## Notes

If you're running ComfyGallery from outside ComfyUI you'll need to provide the ComfyUI root directory to it with the `--comfyui-path` launch argument. 
