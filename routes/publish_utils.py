"""
Publish history utilities for managing undo/redo stacks.

This module provides utilities for managing publication history stacks
for destinations, enabling undo/redo functionality while gracefully
handling cases where no history exists.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json
from datetime import datetime

from utils.logger import info, error, warning, debug
from routes.bucketer import load_meta, save_meta
from routes.publisher import get_published_info

# Configuration
DEFAULT_MAX_STACK_SIZE = 99


class PublishHistoryManager:
    """Manages publication history stack for a destination."""
    
    def __init__(self, destination_id: str, max_stack_size: int = DEFAULT_MAX_STACK_SIZE):
        self.destination_id = destination_id
        self.max_stack_size = max_stack_size
    
    def _get_history_data(self) -> Dict[str, Any]:
        """Get current history data from bucket metadata, creating if needed."""
        try:
            meta = load_meta(self.destination_id)
            
            # Initialize history structure if it doesn't exist
            if "published_meta" not in meta:
                meta["published_meta"] = {}
            
            published_meta = meta["published_meta"]
            
            # If no history stack exists, initialize with current published image if available
            if "history_stack" not in published_meta:
                published_meta["history_stack"] = []
                published_meta["current_pointer"] = 0
                
                # Try to get current published image to seed the stack
                current_published = get_published_info(self.destination_id)
                if current_published and current_published.get("published"):
                    initial_entry = {
                        "filename": current_published["published"],
                        "published_at": current_published.get("published_at", ""),
                        "raw_url": current_published.get("raw_url", ""),
                        "thumbnail_url": current_published.get("thumbnail_url", ""),
                        "metadata": current_published.get("meta", {})
                    }
                    published_meta["history_stack"].append(initial_entry)
                    info(f"[history] Initialized history stack for {self.destination_id} with current image")
                else:
                    info(f"[history] Initialized empty history stack for {self.destination_id}")
                
                # Save the initialized structure
                save_meta(self.destination_id, meta)
            
            return published_meta
            
        except Exception as e:
            error(f"[history] Error getting history data for {self.destination_id}: {e}")
            # Return minimal structure on error
            return {
                "history_stack": [],
                "current_pointer": 0
            }
    
    def _save_history_data(self, history_data: Dict[str, Any]) -> bool:
        """Save history data back to bucket metadata."""
        try:
            meta = load_meta(self.destination_id)
            # Merge history data with existing published_meta instead of overwriting
            if "published_meta" not in meta:
                meta["published_meta"] = {}
            
            # Update only the history-related fields
            meta["published_meta"]["history_stack"] = history_data.get("history_stack", [])
            meta["published_meta"]["current_pointer"] = history_data.get("current_pointer", 0)
            
            save_meta(self.destination_id, meta)
            return True
        except Exception as e:
            error(f"[history] Error saving history data for {self.destination_id}: {e}")
            return False
    
    def push_new_image(self, image_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add new image to history stack, truncating any future history.
        
        Args:
            image_info: Dictionary containing filename, published_at, raw_url, 
                       thumbnail_url, and metadata
        
        Returns:
            Dictionary with operation result and current state
        """
        try:
            history_data = self._get_history_data()
            stack = history_data["history_stack"]
            pointer = history_data["current_pointer"]
            
            # Remove all entries above current pointer (truncate future history)
            if pointer < len(stack) - 1:
                stack = stack[:pointer + 1]
                debug(f"[history] Truncated future history for {self.destination_id}, new stack size: {len(stack)}")
            
            # Add new image at the front (index 0)
            new_entry = {
                "filename": image_info["filename"],
                "published_at": image_info.get("published_at", datetime.utcnow().isoformat() + "Z"),
                "raw_url": image_info.get("raw_url", ""),
                "thumbnail_url": image_info.get("thumbnail_url", ""),
                "metadata": image_info.get("metadata", {})
            }
            
            # Insert at beginning and adjust indices
            stack.insert(0, new_entry)
            
            # Trim stack if it exceeds max size
            if len(stack) > self.max_stack_size:
                stack = stack[:self.max_stack_size]
                debug(f"[history] Trimmed stack to {self.max_stack_size} entries for {self.destination_id}")
            
            # Reset pointer to 0 (newest image)
            history_data["history_stack"] = stack
            history_data["current_pointer"] = 0
            
            # Save changes
            if self._save_history_data(history_data):
                info(f"[history] Added new image to history for {self.destination_id}, stack size: {len(stack)}")
                return {
                    "success": True,
                    "current_image": new_entry,
                    "pointer_position": 0,
                    "stack_size": len(stack),
                    "can_undo": len(stack) > 1,
                    "can_redo": False
                }
            else:
                return {"success": False, "error": "Failed to save history data"}
                
        except Exception as e:
            error(f"[history] Error pushing new image for {self.destination_id}: {e}")
            return {"success": False, "error": str(e)}
    
    def undo(self) -> Dict[str, Any]:
        """
        Move pointer back in history (to older image).
        
        Returns:
            Dictionary with operation result and current state
        """
        try:
            history_data = self._get_history_data()
            stack = history_data["history_stack"]
            pointer = history_data["current_pointer"]
            
            # Check if we can undo
            if len(stack) == 0:
                return {
                    "success": False,
                    "error": "No history available",
                    "can_undo": False,
                    "can_redo": False
                }
            
            if pointer >= len(stack) - 1:
                return {
                    "success": False,
                    "error": "Already at oldest image",
                    "current_image": stack[pointer] if pointer < len(stack) else None,
                    "pointer_position": pointer,
                    "stack_size": len(stack),
                    "can_undo": False,
                    "can_redo": pointer > 0
                }
            
            # Move pointer back (to older image)
            new_pointer = pointer + 1
            history_data["current_pointer"] = new_pointer
            
            # Save changes
            if self._save_history_data(history_data):
                current_image = stack[new_pointer]
                info(f"[history] Undo for {self.destination_id}, moved to pointer {new_pointer}")
                return {
                    "success": True,
                    "current_image": current_image,
                    "pointer_position": new_pointer,
                    "stack_size": len(stack),
                    "can_undo": new_pointer < len(stack) - 1,
                    "can_redo": new_pointer > 0
                }
            else:
                return {"success": False, "error": "Failed to save history data"}
                
        except Exception as e:
            error(f"[history] Error during undo for {self.destination_id}: {e}")
            return {"success": False, "error": str(e)}
    
    def redo(self) -> Dict[str, Any]:
        """
        Move pointer forward in history (to newer image).
        
        Returns:
            Dictionary with operation result and current state
        """
        try:
            history_data = self._get_history_data()
            stack = history_data["history_stack"]
            pointer = history_data["current_pointer"]
            
            # Check if we can redo
            if len(stack) == 0:
                return {
                    "success": False,
                    "error": "No history available",
                    "can_undo": False,
                    "can_redo": False
                }
            
            if pointer <= 0:
                return {
                    "success": False,
                    "error": "Already at newest image",
                    "current_image": stack[0] if len(stack) > 0 else None,
                    "pointer_position": pointer,
                    "stack_size": len(stack),
                    "can_undo": len(stack) > 1,
                    "can_redo": False
                }
            
            # Move pointer forward (to newer image)
            new_pointer = pointer - 1
            history_data["current_pointer"] = new_pointer
            
            # Save changes
            if self._save_history_data(history_data):
                current_image = stack[new_pointer]
                info(f"[history] Redo for {self.destination_id}, moved to pointer {new_pointer}")
                return {
                    "success": True,
                    "current_image": current_image,
                    "pointer_position": new_pointer,
                    "stack_size": len(stack),
                    "can_undo": new_pointer < len(stack) - 1,
                    "can_redo": new_pointer > 0
                }
            else:
                return {"success": False, "error": "Failed to save history data"}
                
        except Exception as e:
            error(f"[history] Error during redo for {self.destination_id}: {e}")
            return {"success": False, "error": str(e)}
    
    def get_current_image(self) -> Optional[Dict[str, Any]]:
        """Get the image at the current pointer position."""
        try:
            history_data = self._get_history_data()
            stack = history_data["history_stack"]
            pointer = history_data["current_pointer"]
            
            if len(stack) == 0 or pointer >= len(stack):
                return None
            
            return stack[pointer]
            
        except Exception as e:
            error(f"[history] Error getting current image for {self.destination_id}: {e}")
            return None
    
    def get_stack_info(self) -> Dict[str, Any]:
        """Get current stack status and navigation info."""
        try:
            history_data = self._get_history_data()
            stack = history_data["history_stack"]
            pointer = history_data["current_pointer"]
            
            return {
                "current_pointer": pointer,
                "stack_size": len(stack),
                "can_undo": pointer < len(stack) - 1,
                "can_redo": pointer > 0,
                "current_image": stack[pointer] if pointer < len(stack) else None
            }
            
        except Exception as e:
            error(f"[history] Error getting stack info for {self.destination_id}: {e}")
            return {
                "current_pointer": 0,
                "stack_size": 0,
                "can_undo": False,
                "can_redo": False,
                "current_image": None
            }


def get_history_manager(destination_id: str) -> PublishHistoryManager:
    """Get a history manager instance for a destination."""
    return PublishHistoryManager(destination_id)


def record_new_publish(destination_id: str, filename: str, published_at: str, 
                      raw_url: str, thumbnail_url: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Record a new publish operation in the history stack.
    
    This should be called when a fresh image is published (not during undo/redo).
    """
    manager = get_history_manager(destination_id)
    
    image_info = {
        "filename": filename,
        "published_at": published_at,
        "raw_url": raw_url,
        "thumbnail_url": thumbnail_url,
        "metadata": metadata or {}
    }
    
    return manager.push_new_image(image_info)


def undo_publish(destination_id: str) -> Dict[str, Any]:
    """Undo the last publish operation."""
    manager = get_history_manager(destination_id)
    return manager.undo()


def redo_publish(destination_id: str) -> Dict[str, Any]:
    """Redo a previously undone publish operation."""
    manager = get_history_manager(destination_id)
    return manager.redo()


def get_publish_history_info(destination_id: str) -> Dict[str, Any]:
    """Get current publish history information."""
    manager = get_history_manager(destination_id)
    return manager.get_stack_info()


def undo_for_targets(targets: List[str]) -> Dict[str, Any]:
    """
    Perform undo operation for multiple targets.
    
    Args:
        targets: List of destination IDs to undo
        
    Returns:
        Dictionary with overall result and per-target results
    """
    from routes.publisher import publish_to_destination, _record_publish
    from routes.bucketer import bucket_path
    from pathlib import Path
    
    results = {}
    success_count = 0
    error_count = 0
    
    for target in targets:
        try:
            info(f"[undo_targets] Processing undo for target: {target}")
            
            # Attempt to undo in history stack
            undo_result = undo_publish(target)
            
            if not undo_result.get("success"):
                results[target] = {
                    "success": False,
                    "error": undo_result.get("error", "Cannot undo"),
                    "can_undo": undo_result.get("can_undo", False),
                    "can_redo": undo_result.get("can_redo", False)
                }
                error_count += 1
                continue
            
            # Get the image we need to display
            current_image = undo_result.get("current_image")
            if not current_image:
                results[target] = {
                    "success": False,
                    "error": "No image to display"
                }
                error_count += 1
                continue
            
            filename = current_image.get("filename")
            if not filename:
                results[target] = {
                    "success": False,
                    "error": "Invalid image data"
                }
                error_count += 1
                continue
            
            # Find the image file in the bucket
            bucket_dir = bucket_path(target)
            image_path = bucket_dir / filename
            
            if not image_path.exists():
                results[target] = {
                    "success": False,
                    "error": f"Image file {filename} not found"
                }
                error_count += 1
                continue
            
            # Publish the image (this will display it but not add to history)
            info(f"[undo_targets] Publishing previous image for {target}: {filename}")
            publish_result = publish_to_destination(
                source=image_path,
                publish_destination_id=target,
                skip_bucket=True,  # Don't add to bucket again
                silent=False,  # Show overlay
                metadata=current_image.get("metadata", {}),
                is_history_navigation=True  # Don't overwrite history pointer, but do update published_meta
            )
            
            if not publish_result.get("success"):
                results[target] = {
                    "success": False,
                    "error": "Failed to display previous image"
                }
                error_count += 1
                continue
            
            # Note: We don't call _record_publish here because the history manager
            # already updated the pointer, and _record_publish would overwrite it
            
            results[target] = {
                "success": True,
                "filename": filename,
                "can_undo": undo_result.get("can_undo"),
                "can_redo": undo_result.get("can_redo")
            }
            success_count += 1
            
        except Exception as e:
            error(f"[undo_targets] Error during undo for {target}: {e}")
            results[target] = {
                "success": False,
                "error": f"Undo operation failed: {str(e)}"
            }
            error_count += 1
    
    return {
        "overall_success": success_count > 0,
        "success_count": success_count,
        "error_count": error_count,
        "results": results
    }


def redo_for_targets(targets: List[str]) -> Dict[str, Any]:
    """
    Perform redo operation for multiple targets.
    
    Args:
        targets: List of destination IDs to redo
        
    Returns:
        Dictionary with overall result and per-target results
    """
    from routes.publisher import publish_to_destination, _record_publish
    from routes.bucketer import bucket_path
    from pathlib import Path
    
    results = {}
    success_count = 0
    error_count = 0
    
    for target in targets:
        try:
            info(f"[redo_targets] Processing redo for target: {target}")
            
            # Attempt to redo in history stack
            redo_result = redo_publish(target)
            
            if not redo_result.get("success"):
                results[target] = {
                    "success": False,
                    "error": redo_result.get("error", "Cannot redo"),
                    "can_undo": redo_result.get("can_undo", False),
                    "can_redo": redo_result.get("can_redo", False)
                }
                error_count += 1
                continue
            
            # Get the image we need to display
            current_image = redo_result.get("current_image")
            if not current_image:
                results[target] = {
                    "success": False,
                    "error": "No image to display"
                }
                error_count += 1
                continue
            
            filename = current_image.get("filename")
            if not filename:
                results[target] = {
                    "success": False,
                    "error": "Invalid image data"
                }
                error_count += 1
                continue
            
            # Find the image file in the bucket
            bucket_dir = bucket_path(target)
            image_path = bucket_dir / filename
            
            if not image_path.exists():
                results[target] = {
                    "success": False,
                    "error": f"Image file {filename} not found"
                }
                error_count += 1
                continue
            
            # Publish the image (this will display it but not add to history)
            info(f"[redo_targets] Publishing next image for {target}: {filename}")
            publish_result = publish_to_destination(
                source=image_path,
                publish_destination_id=target,
                skip_bucket=True,  # Don't add to bucket again
                silent=False,  # Show overlay
                metadata=current_image.get("metadata", {}),
                is_history_navigation=True  # Don't overwrite history pointer, but do update published_meta
            )
            
            if not publish_result.get("success"):
                results[target] = {
                    "success": False,
                    "error": "Failed to display next image"
                }
                error_count += 1
                continue
            
            # Note: We don't call _record_publish here because the history manager
            # already updated the pointer, and _record_publish would overwrite it
            
            results[target] = {
                "success": True,
                "filename": filename,
                "can_undo": redo_result.get("can_undo"),
                "can_redo": redo_result.get("can_redo")
            }
            success_count += 1
            
        except Exception as e:
            error(f"[redo_targets] Error during redo for {target}: {e}")
            results[target] = {
                "success": False,
                "error": f"Redo operation failed: {str(e)}"
            }
            error_count += 1
    
    return {
        "overall_success": success_count > 0,
        "success_count": success_count,
        "error_count": error_count,
        "results": results
    } 