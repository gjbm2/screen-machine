from collections import deque
from typing import Dict, Any, List, Optional, Union, Tuple
from datetime import datetime
from utils.logger import debug, info, error

class InstructionQueue:
    """
    Manages a per-destination queue of instructions with support for urgent/important preemption.
    Each instruction in the queue is stored with flags indicating if it's important or urgent.
    """
    def __init__(self):
        """Initialize an empty instruction queue."""
        self.queue = deque()

    def push_block(self, instructions: List[Dict[str, Any]], important: bool = False, urgent: bool = False) -> None:
        """
        Push a block of instructions onto the queue with appropriate flags.
        
        Args:
            instructions: List of instruction dictionaries to add to the queue
            important: If True, these instructions are marked as important and will not be removed by urgent preemption
            urgent: If True, these instructions are inserted at the front of the queue and non-important instructions are removed
        """
        if not instructions:
            return
            
        entries = [self.make_instruction_entry(instr, important, urgent) for instr in instructions]
        
        if urgent:
            # If urgent, remove all non-important instructions and insert at front
            self.remove_non_important()
            # Insert urgent instructions at the front (preserving order)
            for entry in reversed(entries):
                self.queue.appendleft(entry)
            debug(f"Added {len(entries)} urgent instructions to front of queue (now size {len(self.queue)})")
        elif important:
            # If important but not urgent, append to the end of the queue
            self.queue.extend(entries)
            debug(f"Added {len(entries)} important instructions to end of queue (now size {len(self.queue)})")
        elif not self.queue:
            # If not important and not urgent, only add if queue is empty
            self.queue.extend(entries)
            debug(f"Added {len(entries)} normal instructions to empty queue")
        else:
            debug(f"Skipped {len(entries)} non-important instructions (queue not empty)")

    def pop_next(self) -> Optional[Dict[str, Any]]:
        """
        Pop and return the next instruction entry from the queue.
        
        Returns:
            A dictionary with keys 'instruction', 'important', 'urgent', or None if queue is empty
        """
        if self.queue:
            return self.queue.popleft()
        return None

    def remove_non_important(self) -> None:
        """Remove all non-important instructions from the queue (used for urgent preemption)."""
        old_size = len(self.queue)
        self.queue = deque([entry for entry in self.queue if entry['important']])
        debug(f"Removed {old_size - len(self.queue)} non-important instructions from queue")

    def clear(self) -> None:
        """Clear the queue (e.g., on unload)."""
        self.queue.clear()
        debug("Queue cleared")

    def is_empty(self) -> bool:
        """Return True if the queue is empty."""
        return not self.queue
        
    def get_size(self) -> int:
        """Return the current size of the queue."""
        return len(self.queue)

    @staticmethod
    def make_instruction_entry(instruction: Dict[str, Any], important: bool, urgent: bool) -> Dict[str, Any]:
        """
        Create an instruction entry for the queue.
        
        Args:
            instruction: The instruction dictionary to execute
            important: Whether this instruction is important and should not be removed by urgent preemption
            urgent: Whether this instruction is urgent and should be executed immediately
            
        Returns:
            A dictionary with keys 'instruction', 'important', 'urgent'
        """
        return {
            'instruction': instruction,
            'important': important,
            'urgent': urgent
        }

# Per-destination instruction queues
instruction_queues: Dict[str, InstructionQueue] = {}

def get_instruction_queue(publish_destination: str) -> InstructionQueue:
    """
    Get or create an instruction queue for a destination.
    
    Args:
        publish_destination: The destination ID
        
    Returns:
        The InstructionQueue for this destination
    """
    if publish_destination not in instruction_queues:
        instruction_queues[publish_destination] = InstructionQueue()
    return instruction_queues[publish_destination]

def clear_instruction_queue(publish_destination: str) -> None:
    """
    Clear the instruction queue for a destination.
    
    Args:
        publish_destination: The destination ID
    """
    if publish_destination in instruction_queues:
        instruction_queues[publish_destination].clear()

def check_urgent_events(publish_destination: str) -> Optional[Dict[str, Any]]:
    """
    Check for urgent events for a publish destination.
    
    This checks for special events like __terminate__, __terminate_immediate__, __exit_block__,
    which should be processed urgently.
    
    Args:
        publish_destination: The destination ID
        
    Returns:
        A dictionary containing event information if an urgent event is found, or None
    """
    from routes.scheduler_utils import pop_next_event
    
    URGENT_EVENTS = ["__terminate__", "__terminate_immediate__", "__exit_block__"]
    
    for event_key in URGENT_EVENTS:
        try:
            now = datetime.now()
            event_entry = pop_next_event(publish_destination, event_key, now, event_trigger_mode=True)
            if event_entry:
                info(f"Found urgent event: {event_key} for {publish_destination}")
                
                # Extract prevent_unload from payload if available
                prevent_unload = False
                if event_entry.payload and isinstance(event_entry.payload, dict):
                    prevent_unload = event_entry.payload.get("prevent_unload", False)
                
                # Create a synthetic block for the urgent event
                if event_key == "__terminate__":
                    # For normal terminate, we need to run final actions
                    return {
                        "key": event_key,
                        "block": [{"action": "terminate", "mode": "normal", "prevent_unload": prevent_unload, "from_event": True}],
                        "payload": event_entry.payload
                    }
                elif event_key == "__terminate_immediate__":
                    # For immediate terminate, skip final actions
                    return {
                        "key": event_key,
                        "block": [{"action": "terminate", "mode": "immediate", "prevent_unload": prevent_unload, "from_event": True}],
                        "payload": event_entry.payload
                    }
                elif event_key == "__exit_block__":
                    # For exit block, just exit the current block
                    return {
                        "key": event_key,
                        "block": [{"action": "terminate", "mode": "block", "from_event": True}],
                        "payload": event_entry.payload
                    }
        except Exception as e:
            error(f"Error checking urgent event {event_key}: {str(e)}")
    
    return None

def process_triggers(schedule: Dict[str, Any], now: datetime, publish_destination: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Process all triggers and return a list of trigger info, including the instructions to run.
    
    Args:
        schedule: The current schedule
        now: Current datetime
        publish_destination: The destination ID
        context: Current context
        
    Returns:
        List of trigger info dictionaries, each with 'block', 'important', and 'urgent' keys
    """
    from routes.scheduler import resolve_schedule
    
    triggers = []
    
    # Get instructions from regular schedule resolution
    instructions = resolve_schedule(schedule, now, publish_destination, include_initial_actions=False, context=context)
    
    if instructions:
        # For now, treat all regular triggers as non-urgent, non-important
        # Later you may want to extract importance/urgency from the trigger itself
        triggers.append({
            "block": instructions,
            "important": False,
            "urgent": False
        })
    
    return triggers 