from collections import deque
from typing import Dict, Any, List, Optional, Union, Tuple
from datetime import datetime, timedelta
from utils.logger import debug, info, error
import time

class InstructionQueue:
    """
    Manages a per-destination queue of instructions with support for urgent/important preemption.
    Each instruction in the queue is stored with flags indicating if it's important or urgent.
    """
    def __init__(self):
        """Initialize an empty instruction queue."""
        self.queue = deque()
        self._last_urgent_log_time = 0  # Store rate-limiting state as instance variable

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

    def pop_next(self, urgent_only: bool = False) -> Optional[Dict[str, Any]]:
        """
        Pop and return the next instruction entry from the queue.
        
        Args:
            urgent_only: If True, only pop an urgent instruction (used during wait interruption)
        
        Returns:
            A dictionary with keys 'instruction', 'important', 'urgent', or None if queue is empty
        """
        if not self.queue:
            return None
            
        # If we're looking for urgent entries specifically (during wait interruption)
        if urgent_only:
            # First check if we have an urgent entry (peek sets _urgent_index)
            urgent_entry = self.peek_next_urgent()
            if urgent_entry and '_urgent_index' in urgent_entry:
                # Found urgent entry - get its position
                urgent_index = urgent_entry['_urgent_index']
                # Remove the position marker
                del urgent_entry['_urgent_index']
                
                # If it's the first item, use popleft for efficiency
                if urgent_index == 0:
                    return self.queue.popleft()
                else:
                    # Otherwise, we need to remove it by position
                    # Convert to list, remove item, convert back to deque
                    queue_list = list(self.queue)
                    result = queue_list.pop(urgent_index)
                    self.queue = deque(queue_list)
                    debug(f"Removed urgent instruction at position {urgent_index} (not at front of queue)")
                    return result
            return None  # No urgent entries found when urgent_only=True
            
        # Normal case - just get the next instruction
        return self.queue.popleft()

    def peek_next_urgent(self) -> Optional[Dict[str, Any]]:
        """
        Look at the next urgent instruction without removing it.
        
        Returns:
            The first urgent instruction entry, or None if no urgent entries are in the queue
        """
        # Find the first urgent item in the queue
        if not self.queue:
            return None
        
        # Rate limit the logging during waits
        current_time = time.time()
        should_log = (current_time - self._last_urgent_log_time) > 30.0
        
        if should_log:
            self._last_urgent_log_time = current_time
            debug(f"peek_next_urgent: Checking {len(self.queue)} items in queue for urgent flag")
        
        # Record details of items for debugging
        urgent_found = False
        urgent_index = -1
        urgent_entry = None
        
        for i, entry in enumerate(self.queue):
            is_urgent = entry.get('urgent', False)
            action = entry.get('instruction', {}).get('action', 'unknown')
            if is_urgent:
                urgent_found = True
                urgent_index = i
                urgent_entry = entry
                # Always log found urgent instructions regardless of rate limiting
                debug(f"peek_next_urgent: Found URGENT instruction at position {i}: {action}")
                # Store the index so we can remove it directly later
                entry['_urgent_index'] = i
                return entry
            elif should_log:
                debug(f"peek_next_urgent: Non-urgent instruction at position {i}: {action}")
        
        if not urgent_found and should_log:
            debug("peek_next_urgent: No urgent instructions found in queue")
        
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
    
    # Add rate-limiting for debug logging
    if not hasattr(process_triggers, '_last_debug_log_time'):
        process_triggers._last_debug_log_time = {}
    
    should_log = False
    current_time = time.time()
    if publish_destination not in process_triggers._last_debug_log_time or \
       (current_time - process_triggers._last_debug_log_time.get(publish_destination, 0)) > 30:
        should_log = True
        process_triggers._last_debug_log_time[publish_destination] = current_time
    
    # Check if we're in a wait state
    is_in_wait_state = "wait_until" in context
    if is_in_wait_state and should_log:
        debug(f"process_triggers: WAIT STATE ACTIVE - only urgent triggers will interrupt the wait")
    
    # Get triggers with urgency/importance information from schedule resolution
    # resolve_schedule now returns list of dicts with block, urgent, important flags
    trigger_data = resolve_schedule(schedule, now, publish_destination, include_initial_actions=False, context=context)
    
    if trigger_data and should_log:
        debug(f"process_triggers: Received {len(trigger_data)} trigger data entries from resolve_schedule")
        
        if is_in_wait_state:
            # In wait state, count how many could potentially interrupt
            urgent_count = 0
            for entry in trigger_data:
                if entry.get("urgent", False):
                    urgent_count += 1
            
            if urgent_count:
                debug(f"process_triggers: Found {urgent_count} URGENT triggers that could interrupt the wait state")
            else:
                debug(f"process_triggers: None of the triggers are marked as urgent, wait will continue")
        
        for entry in trigger_data:
            # Log details for debugging
            urgent = entry.get("urgent", False)
            important = entry.get("important", False)
            block_size = len(entry.get("block", []))
            source = entry.get("source", "unknown")
            flags = []
            if urgent:
                flags.append("urgent")
            if important:
                flags.append("important")
            flags_str = f" ({', '.join(flags)})" if flags else ""
            
            # Add wait state status to log when relevant
            wait_msg = ""
            if is_in_wait_state:
                if urgent:
                    wait_msg = " - will interrupt wait"
                else:
                    wait_msg = " - will NOT interrupt wait"
            
            debug(f"process_triggers: Found {block_size} instructions from {source}{flags_str}{wait_msg}")
    
    return trigger_data 