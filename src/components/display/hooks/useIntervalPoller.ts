
import { useState, useEffect, useRef } from 'react';

export const useIntervalPoller = (
  enabled: boolean,
  intervalSeconds: number,
  callback: () => void,
  dependencies: any[] = []
) => {
  // Always return not polling since we're removing automated functionality
  // This is a stub implementation that doesn't actually poll
  return { isPolling: false };
};
