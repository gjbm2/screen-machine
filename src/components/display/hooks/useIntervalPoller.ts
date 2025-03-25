
import { useState, useEffect, useRef } from 'react';

export const useIntervalPoller = (
  enabled: boolean,
  intervalSeconds: number,
  callback: () => void,
  dependencies: any[] = []
) => {
  const [isPolling, setIsPolling] = useState(false);
  
  // Always return not polling since we're removing automated functionality
  return { isPolling: false };
};
