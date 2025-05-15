/**
 * Utility functions for working with scheduler schedules
 */

/**
 * Extract all event triggers from a schedule
 * @param schedule The schedule object to analyze
 * @returns Array of event trigger keys found in the schedule
 */
export function extractEventTriggers(schedule: any): string[] {
  if (!schedule || typeof schedule !== 'object') {
    return [];
  }

  const eventTriggers = new Set<string>();
  
  // Check if the schedule has triggers
  if (Array.isArray(schedule.triggers)) {
    // Scan each trigger for events
    schedule.triggers.forEach((trigger: any) => {
      // If it's an event trigger, extract the key
      if (trigger.type === 'event' && trigger.value) {
        eventTriggers.add(trigger.value);
      }
    });
  }
  
  return Array.from(eventTriggers);
} 