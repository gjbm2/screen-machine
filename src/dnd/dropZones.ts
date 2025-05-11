export const DROP_ZONES = {
  PROMPT: 'drop-prompt',
  TAB_PREFIX: 'drop-tab-',      // + <tabId>
  PUBLISHED: 'drop-published',
  SECTION_PREFIX: 'drop-section-', // + <sectionId>
} as const;

export type DropZoneId =
  | typeof DROP_ZONES.PROMPT
  | typeof DROP_ZONES.PUBLISHED
  | `${typeof DROP_ZONES.TAB_PREFIX}${string}`
  | `${typeof DROP_ZONES.SECTION_PREFIX}${string}`; 