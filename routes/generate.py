import argparse
import random
import requests
import json
import os
import re
import sys
import textwrap
from dotenv import load_dotenv, find_dotenv
from datetime import datetime
import routes.openai
from routes.utils import findfile
import routes.display
from time import sleep, time 
import uuid
from overlay_ws_server import job_progress_listeners_latest
from urllib.parse import urlparse

# Runpod
import runpod
import requests

# image processing
from PIL import Image
import piexif
import piexif.helper
from io import BytesIO

###########
# LOGGING #
###########

load_dotenv(find_dotenv())

def debug(message: str, **kwargs):
    """Log a debug message"""
    if app_logging:
        return utils.logger.debug(message, **kwargs)
    else:
        print(f"DEBUG: {message}", **kwargs)

def info(message: str, **kwargs):
    """Log an info message"""
    if app_logging:
        return utils.logger.info(message, **kwargs)
    else:
        print(f"INFO: {message}", **kwargs)

def warning(message: str, **kwargs):
    """Log a warning message"""
    if app_logging:
        return utils.logger.warning(message, **kwargs)
    else:
        print(f"WARNING: {message}", **kwargs)

def error(message: str, **kwargs):
    """Log an error message"""
    if app_logging:
        return utils.logger.error(message, **kwargs)
    else:
        print(f"ERROR: {message}", **kwargs)
        
try:
    from utils.logger import log_to_console, info, error, warning, debug, console_logs
except ImportError:
    app_logging = None
   
########
# MAIN #
########

def save_video_with_metadata(url: str, img_metadata: dict, save_path: str):
    # Download the video
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    info(f"Saved MP4 to {save_path}")

    # Save metadata sidecar as .json
    metadata_path = save_path + ".json"
    with open(metadata_path, "w", encoding="utf-8") as meta_file:
        json.dump(img_metadata, meta_file, ensure_ascii=False, indent=2)

    info(f"Saved metadata to {metadata_path}")

def save_jpeg_with_metadata(url, img_metadata: dict, save_path: str):
    response = requests.get(url)
    response.raise_for_status()
    img = Image.open(BytesIO(response.content)).convert("RGB")

    # Build EXIF with JSON-formatted metadata
    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    comment = json.dumps(img_metadata, ensure_ascii=False, indent=None)
    user_comment = piexif.helper.UserComment.dump(comment, encoding="unicode")
    exif_dict["Exif"][piexif.ExifIFD.UserComment] = user_comment

    exif_bytes = piexif.dump(exif_dict)
    img.save(save_path, "JPEG", exif=exif_bytes, quality=95)
    info(f"Saved JPEG with metadata to {save_path}")
    
def detect_file_type(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith(".mp4"):
        return "video"
    elif path.endswith(".jpg") or path.endswith(".jpeg") or path.endswith(".png"):
        return "image"
    return "unknown"

def loosely_matches(a, b):
    # Normalize both strings to lowercase, strip spaces
    a = a.lower().strip()
    b = b.lower().strip()

    # Allow substring match or abbreviation match
    return a in b or b in a or re.sub(r'\s+', '', a) in re.sub(r'\s+', '', b)

# Argument parsing

def get_input_text(input_arg):
    # Check if the input is a file path
    if os.path.isfile(input_arg):
        with open(input_arg, 'r', encoding='utf-8') as file:
            file_contents = file.read()
            return file_contents
    else:
        # Otherwise, return the input argument directly as text
        return input_arg

# Dictionary replacer
def update_workflow(json_data, replacements):
    """
    Updates 'inputs' of matching nodes in a workflow JSON based on regex matches
    against the _meta.title field (case-insensitive).

    :param json_data: A Python dictionary loaded from a workflow JSON file.
    :param replacements: A dict where:
        - Keys are regex patterns (e.g., r"KSampler")
        - Values are dicts of input_key: new_value pairs to apply to matching nodes.
    :return: Updated JSON dictionary with replacements applied.
    """
    for node_id, node_def in json_data.items():
        node_title = node_def.get("_meta", {}).get("title", "")

        for pattern, input_changes in replacements.items():
            if re.search(pattern, node_title, flags=re.IGNORECASE):
                for input_key, new_value in input_changes.items():
                    if new_value is None:
                        continue
                    if input_key in node_def.get("inputs", {}):
                        node_def["inputs"][input_key] = new_value
                        if len(str(new_value)) > 60:
                            info(
                                f"  - Replaced {input_key} in '{node_title}' with:\n"
                                f"{textwrap.indent(textwrap.fill(str(new_value), width=80), '      ')}"
                            )
                        else:
                            info(f"  - Replaced {input_key} in '{node_title}' with: {new_value}")

    return json_data

def start(
        prompt: str | None = None, 
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        cfg: float | None = None,
        batch: int | None = None,
        pod: str | None = None,
        scale: int | None = None,
        setwallpaper: int | None = None,
        refine: str | None = None,
        workflow: str | None = None,
        seed: int | None = None,
        timeout: int | None = None,
        suppress: bool | None = None,
        metaprompt: bool | None = None,
        images: list[dict[str, str]] | None = None,
        negativeprompt: str | None = None,
        upscaler: str | None = None,
        lora: str | None = None,
        video_length: int | None = None,
        lora_strength: float | None = None,
        cli_args=None,
        publish_destination: str | None = None,
        **kwargs
        ):

    # Basic init
    info("Generator starting.")
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    missing_vars = [var for var in ["OPENAI_API_KEY", "RUNPOD_API_KEY", "RUNPOD_ID"] if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"X Missing environment variables: {', '.join(missing_vars)}. Fatal.")
        
    # Get params
    parser = get_parser() 
    if cli_args is not None:                    # called from command line
        args_namespace = parser.parse_args(cli_args)
    else:                                       # called as  a function
        defaults = vars(parser.parse_args([]))  # extract defaults
        
        provided_args = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "batch": batch,
            "pod": pod,
            "cfg": cfg,
            "scale": scale,
            "setwallpaper": setwallpaper,
            "refine": refine,
            "workflow": workflow,
            "seed": seed,
            "timeout": timeout,
            "negativeprompt": negativeprompt,
            "upscaler": upscaler,
            "lora": lora,
            "lora_strength": lora_strength,
            "suppress": suppress,
            "video_length" : video_length,
            "metaprompt": metaprompt
        }
        provided_args["images"] = images        # include images if any

        # Merge defaults with provided arguments (use provided if not None, else default)
        final_args = {**defaults}
        final_args.update({k: v for k, v in provided_args.items() if v is not None})
        info(f"Kwags: {kwargs}")
        final_args.update(kwargs)
        
        #final_args = {k: v if v is not None else defaults.get(k, None) for k, v in provided_args.items()}
        args_namespace = argparse.Namespace(**final_args)
        
        #info(f"Args_namespace: {args_namespace}")

    # Validation steps
    if not args_namespace.prompt and not args_namespace.images: 
        raise ValueError("A prompt or image must be provided.")
    
    # Get destinations data and see if we have a size constraint
    if publish_destination:
        info(f"publish_destination: {publish_destination}")
        with open(findfile("publish-destinations.json"), "r") as file:
            publish_destinations = json.load(file)
        
        target_config = next(
            (item for item in publish_destinations if
             loosely_matches(item.get("id", ""), publish_destination) or
             loosely_matches(item.get("name", ""), publish_destination)),
            {}
        )
        
        maxwidth = target_config.get("maxwidth", None)
        maxheight = target_config.get("maxheight", None)
        targetfile=target_config.get('file')
        info(f"Target config: {targetfile}")
        
        # Let the screen know we're generating
        routes.display.send_overlay(
            html = "overlay_generating.html",
            screens = [publish_destination] if isinstance(publish_destination, str) else publish_destination, 
            substitutions = {'{{MESSAGE}}': 'Initialising...'},
            duration = 30000,
            position = "top-left"
        )
    
    #info("Image parameters: " + ", ".join(f"{k}={v}" for k, v in vars(args_namespace).items()))
    
    with open(findfile(args_namespace.workflow), "r") as file:
        workflow_data = json.load(file)
    
    unique_video_name = f"output_video{str(uuid.uuid4())[:8]}"    
    json_replacements = {
        r"{{LATENT-IMAGE}}": {  
            "width": args_namespace.width,
            "height": args_namespace.height,
            "batch_size": args_namespace.batch
        },
        r"{{POSITIVE-PROMPT}}": {  
            "text":  args_namespace.prompt
        },
        r"{{NEGATIVE-PROMPT}}": {  
            "text": vars(args_namespace).get("negativeprompt", None)
        },
        r"{{UPSCALER}}": {
            "scale_by": args_namespace.scale,
            "model_name": vars(args_namespace).get("upscaler", None)
        },
        r"{{SAMPLER}}": {
            "seed": args_namespace.seed,
            "noise_seed": args_namespace.seed,            
            "steps": args_namespace.steps,
            "scheduler": vars(args_namespace).get("scheduler", None),
            "sampler":vars(args_namespace).get("sampler", None),
            "width": args_namespace.width,
            "height": args_namespace.height,
            "cfg": vars(args_namespace).get("cfg", None)
        },
        r"{{LORA}}": {
            "lora_name": vars(args_namespace).get("lora", None),
            "strength_model": vars(args_namespace).get("lora_strength", None),
            "strength_clip": vars(args_namespace).get("lora_strength", None)
        },
        r"{{DOWNSCALER}}": {
            "width": locals().get("maxwidth", 6400),
            "height": locals().get("maxheight", 6400)
        },
        r"{{IMAGE-TO-VIDEO}}": {
            "length": vars(args_namespace).get("video_length", 10)
        },
        r"{{SAVE-WEBM}}": {
            "filename_prefix": unique_video_name
        }
    }
    
    if args_namespace.images and len(args_namespace.images) > 0:
        json_replacements[r"{{LOAD-IMAGE}}"] = {
            "image": args_namespace.images[0].get("name")
        }

    # Compile the final object to submit
    input_payload = {
      "input": {
        "workflow": update_workflow(workflow_data, json_replacements),
      }
    }
    if args_namespace.images:
        input_payload["input"]["images"] = args_namespace.images
    
    # Use the right runpod container id
    with open(findfile("workflows.json")) as f:
        workflow_config = json.load(f)

    runpod_id = next(
        (item.get("runpod_id") for item in workflow_config if item["id"] == args_namespace.workflow and item.get("runpod_id")),
        getattr(args_namespace, "pod", None) or os.getenv("RUNPOD_ID")
    )

    info(f"Workflow: {args_namespace.workflow}")
    info(f"RunPod ID: {runpod_id}")
    runpod.api_key = os.getenv("RUNPOD_API_KEY")
    endpoint = runpod.Endpoint(runpod_id)

    try:
        # Let's go...
        run_request = endpoint.run(input_payload)
        job_id = run_request.job_id

        # get initial status
        status = run_request.status()
        info(f"Initial status: '{status}'")

        #timeout = getattr(args_namespace, "timeout", 600)
        timeout = 100000  # TEMPORARY
        poll_interval = getattr(args_namespace, "poll_interval", 0.5)
        info(f"> Polling RunPod every {poll_interval}s for up to {timeout}s...")

        start_time = time()
        
        try:
            workflow_entry = next(item for item in workflow_config if item["id"] == args_namespace.workflow)
            processing_stages = workflow_entry.get("processing_stages", ["Rendering"])
            if not isinstance(processing_stages, list):
                processing_stages = ["Rendering"]
        except Exception:
            processing_stages = ["Rendering"]

        last_status = None
        last_update_message = ""
        last_update_percentage = ""
        last_max_val = None
        last_value = None
        stage_index = 1

        # To avoid redundant logging
        last_kind = None
        last_details_serialized = None

        while True:
            status = run_request.status()
            progress = job_progress_listeners_latest.get(job_id)

            value = None
            max_val = None
            new_message = last_update_message
            new_percentage = last_update_percentage
            new_stage_index = stage_index
            should_update_overlay = False

            # Detect status change
            if status != last_status:
                info(f"{int(time() - start_time)} RunPod job status: {status}")
                last_status = status
                should_update_overlay = True

            if status == "IN_QUEUE":
                new_message = "Queued..."
                new_percentage = ""
                should_update_overlay = (new_message != last_update_message)

            elif status == "IN_PROGRESS":
                if progress:
                    kind = progress.get("comfy", {}).get("type")
                    details = progress.get("comfy", {}).get("data", {})
                    current_details_serialized = json.dumps(details, sort_keys=True)

                    if kind != last_kind or current_details_serialized != last_details_serialized:
                        info(f"📡 Latest update for job {job_id}: [{kind}] {details}")
                        last_kind = kind
                        last_details_serialized = current_details_serialized

                    if kind == "progress":
                        max_val = int(details.get("max", 1))
                        value = int(details.get("value", 0))

                        # Only update if progress has changed
                        if value != last_value or max_val != last_max_val:
                            # Detect stage change
                            if last_value is not None and (value < last_value or max_val != last_max_val):
                                new_stage_index = min(stage_index + 1, len(processing_stages))

                            stage_label = f"{processing_stages[new_stage_index - 1]} (stage {new_stage_index} of {len(processing_stages)})"
                            new_message = stage_label
                            new_percentage = int(100 * value / max_val)
                            should_update_overlay = True

                    elif kind == "status":
                        queue_remaining = details.get("status", {}).get("exec_info", {}).get("queue_remaining")
                        new_message = "Finalising..." if queue_remaining == 0 else "Queued..."
                        new_percentage = ""
                        should_update_overlay = True

                else:
                    # Fallback if no progress available
                    new_message = "Working..."
                    new_percentage = ""
                    should_update_overlay = True

            elif status in {"FAILED", "CANCELLED"}:
                error_text = "Generation cancelled." if status == "CANCELLED" else "❌ Generation failed."
                # update index page
                routes.display.send_overlay(
                    html="overlay_alert.html",
                    screens="index",
                    substitutions={'{{ALERT_TEXT}}': error_text},
                    duration=5000,
                    job_id=job_id
                )
                if publish_destination:
                    routes.display.send_overlay(
                        html="overlay_alert.html",
                        screens=[publish_destination] if isinstance(publish_destination, str) else publish_destination,
                        substitutions={'{{ALERT_TEXT}}': error_text},
                        duration=5000,
                        position="top-left",
                        clear=True
                    )
                raise RuntimeError(error_text)

            elif status == "COMPLETED":
                output = run_request.output()
                break

            # Update overlay only if there's a real change
            if should_update_overlay and (
                new_message != last_update_message or new_percentage != last_update_percentage
            ):
                # Update index page
                routes.display.send_overlay(
                    html="overlay_generating.html",
                    screens="index",
                    duration=60000,
                    substitutions={
                        '{{MESSAGE}}': new_message,
                        '{{PROGRESS_PERCENT}}': new_percentage
                    },
                    job_id=job_id
                )
                if publish_destination:
                    routes.display.send_overlay(
                        html="overlay_generating.html",
                        screens=[publish_destination] if isinstance(publish_destination, str) else publish_destination,
                        duration=60000,
                        substitutions={
                            '{{MESSAGE}}': new_message,
                            '{{PROGRESS_PERCENT}}': new_percentage
                        },
                        position="top-left",
                        fadein=0,
                        clear=True
                    )
                last_update_message = new_message
                last_update_percentage = new_percentage
                stage_index = new_stage_index
                last_max_val = max_val
                last_value = value

            # Timeout check
            if time() - start_time > timeout:
                raise TimeoutError(f"⏰ RunPod job timed out after {timeout} seconds.")

            sleep(poll_interval)

        
        #run_request = endpoint.run_sync(
        #    input_payload,
        #    timeout=args_namespace.timeout,  # Timeout in seconds.
        #)
                    
        # Success 
        if output["status"]=="success":
            info(f"> Successfully generated image")
            output["seed"] = args_namespace.seed
            output["prompt"] = args_namespace.prompt
            output["negative_prompt"] = vars(args_namespace).get("negativeprompt", None)
                       
            info("\n".join(f"  - {key}: {value}" for key, value in output.items()))
            
            output.update(input_payload)
            
            # We're done with the base-64 image data now--discard it so we aren't carrying it around
            if "images" in input_payload["input"]:
                input_payload["input"]["images"] = [
                    {"name": img.get("name")} for img in input_payload["input"]["images"]
                ]
            if hasattr(args_namespace, "images") and args_namespace.images:
                args_namespace.images = [
                    {"name": img.get("name")} for img in args_namespace.images
                ]
    
            # Where do we need to deliver this?
            if publish_destination:
                file_type = detect_file_type(output["message"])

                if file_type == "image":
                    full_filename = targetfile + ".jpg"
                    targetfilepath = os.path.join(os.getcwd(), "output", full_filename)
                    
                    info(f"📷 Saving image to {targetfilepath}")
                    save_jpeg_with_metadata(
                        url=output["message"],
                        img_metadata=vars(args_namespace),
                        save_path=targetfilepath
                    )

                elif file_type == "video":
                    full_filename = targetfile + ".mp4"
                    targetfilepath = os.path.join(os.getcwd(), "output", full_filename)
                    
                    info(f"🎞️ Saving video to {targetfilepath}")
                    save_video_with_metadata(
                        url=output["message"],
                        img_metadata=vars(args_namespace),
                        save_path=targetfilepath
                    )

                else:
                    raise ValueError(f"❌ Unrecognized file type in URL: {output['message']}")
           
                routes.display.send_overlay(
                    html = "overlay_prompt.html",
                    screens = [publish_destination] if isinstance(publish_destination, str) else publish_destination,
                    substitutions = {
                        '{{PROMPT_TEXT}}': prompt,
                        '{{WORKFLOW_TEXT}}': workflow,
                        '{{WIDTH}}': locals().get("maxwidth", 6400),
                        '{{HEIGHT}}': locals().get("maxheight", 6400),
                        '{{SEED}}':  args_namespace.seed
                    },
                    duration = 30000,
                    clear = True
                )

            display_final_file = f'<a href="{output["message"]}">Done</a>'
            routes.display.send_overlay(
                html = "overlay_generating.html",
                screens = "index",
                substitutions = {
                    '{{MESSAGE}}': display_final_file
                },
                duration = 600000,
                job_id = job_id
            )
            
            return output
        
        else:
            # Let the screen know we're generating
            if publish_destinations:
                routes.display.send_overlay(
                    html = "overlay_alert.html",
                    screens = [publish_destination] if isinstance(publish_destination, str) else publish_destination,
                    substitutions = {'{{ALERT_TEXT}}': '❌ Generation failed.'},
                    duration = 5000,
                    position = "top-left",
                    clear = True
                )
            routes.display.send_overlay(
                html = "overlay_alert.html",
                screens = "index",
                substitutions = {'{{ALERT_TEXT}}': '❌ Generation failed.'},
                duration = 30000,
                job_id = job_id
            )
            raise ValueError(output)
    
    except Exception as e:
        raise ValueError(str(e))


# Handle if called from command line
def get_parser():
    parser = argparse.ArgumentParser(description="Example script with parameters")
    parser.add_argument("prompt", nargs="?", help="Enter prompt text (e.g. 'cat on a sofa')")
    parser.add_argument("--width", type=int, help="Width of image")
    parser.add_argument("--height", type=int, help="Height of image")
    parser.add_argument("--steps", type=int, help="Number of rendering steps")
    parser.add_argument("--scale", default=1, type=int, help="Output scale factor")
    parser.add_argument("--pod", default=os.getenv("RUNPOD_ID"), type=str, help="Runpod ID")
    parser.add_argument(
        "--setwallpaper", 
        nargs="?",               				# Allows zero or one argument
        const=1,             	                        # Used when --refine is present but no value given
        default=None,
        help="Set your desktop wallpaper to this image?"
    )
    parser.add_argument(
        "--refine",
        nargs="?",               				# Allows zero or one argument
        const="refiner-system-prompt.txt",             	# Used when --refine is present but no value given
        default=None,           				# Used when --refine is not provided at all
        help="Optional refinement argument"
    )
    parser.add_argument("--workflow", default="flux1-dev-scale-l.json", type=str, help="Specify the workflow json (with params inserted)")
    parser.add_argument("--seed", default=random.randint(0,99999999), type=int, help="Seed (default = random)")
    parser.add_argument("--timeout", default=300, help="How many seconds to wait.")
    parser.add_argument("--suppress", action="store_true", help="Don't deliver the image to console.")
    parser.add_argument("--batch", default=1, type=int, help="Generate multiple images")
    parser.add_argument("--metaprompt", action="store_true", help="Take the prompt as an instruction to OpenAI to generate a prompt")

    return parser
    
# This was called from command line, so handle accoringly
if __name__ == "__main__":
    try:
        info("Called as CLI.")
        start(cli_args=sys.argv[1:])  # Use CLI args automatically
    except ValueError as e:
        error(e)




"""
def init():
    ########
    # INIT #
    ########



    ########
    # EXEC #
    ########

    print("gjbm2-prompter v0.1 started at ", datetime.now().strftime("%#d-%b-%y %H:%M"))

    raw_prompt_text = get_input_text(args.prompt)

    if args.metaprompt:
      prompt_text = run_prompt(raw_prompt_text, findfile('default-system-prompt.txt'))
    else:
      prompt_text = raw_prompt_text

    ## Refiner ##

    if args.refine is not None:
        refined_prompt = run_prompt(prompt_text,findfile(args.refine))
    else:
        refined_prompt = prompt_text

    ## Workflow ##

    print(f"> Workflow: '{args.workflow}'")

    with open(findfile(args.workflow), "r") as file:
        workflow_data = json.load(file)




    input_payload = {
      "input": {
        "workflow": update_workflow(workflow_data, json_replacements) 
      }
    }

    ## Runpod inference ##

    import runpod
    import requests

    runpod.api_key = os.getenv("RUNPOD_API_KEY")
    endpoint = runpod.Endpoint(args.pod)

    # Let's go...
    run_request = endpoint.run(input_payload)

    # get initial status
    status = run_request.status()
    print(f"> Runpod initial job status (will wait {args.timeout})")

    try:
        run_request = endpoint.run_sync(
            input_payload,
            timeout=args.timeout,  # Timeout in seconds.
        )

        ## Success ##

        if run_request["status"]=="success":

            print(f"> Successfully generated image")
            for key, value in run_request.items(): 
                print(f"  - {key}: {value}")

            if args.setwallpaper is not None:         		# Are we setting wallpaper?
                import ctypes
                import tempfile
                import pywallpaper as wp

                SPI_SETDESKWALLPAPER = 20
                SPI_SETWALLPAPERSTYLE = 6   # Set the wallpaper style (0 = centered, 2 = stretched, 6 = fit, 10 = fill)
                
                responsefile = requests.get(run_request["message"])
                responsefile.raise_for_status()
                # Save temporarily
                file_extension = run_request["message"].split('.')[-1]
                if file_extension.lower() not in ['jpg', 'jpeg', 'bmp', 'png']:
                    file_extension = 'jpg'  # default if unknown
                with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_extension}') as f:
                   f.write(responsefile.content)
                   temp_image_path = f.name

                # Load VirtualDesktopAccessor.dll
                vda = ctypes.WinDLL(os.path.abspath("VirtualDesktopAccessor.dll"))

                def get_current_desktop_number():
                    ""Returns the current virtual desktop number.""
                    return vda.GetCurrentDesktopNumber() + 1  # 0-based index

                def set_wallpaper_for_desktop(image_path, target_desktop):
                    ""Sets wallpaper for a specific virtual desktop.""
                    current_desktop = get_current_desktop_number()
                
                    # Switch to the target desktop if not already on it
                    if current_desktop != target_desktop:
                        vda.GoToDesktopNumber(target_desktop - 1)

                    # Set wallpaper
                    wp.set_wallpaper(temp_image_path)

                    # Switch back to original desktop
                    if current_desktop != target_desktop:
                        vda.GoToDesktopNumber(current_desktop - 1)

                # Example usage
                set_wallpaper_for_desktop(image_path, args.setwallpaper)


                ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, temp_image_path, 2)
                print("Wallpaper: ",run_request["message"])
                print("> Changed wallpaper")

            if not args.suppress:            	# Feed the image back to user
                import webbrowser
                webbrowser.open(run_request["message"])
                print("> Delivered to web browser")

        else:
            print(run_request)

    except TimeoutError:
        print("X Job timed out.")
        sys.exit(2)
        
        """
