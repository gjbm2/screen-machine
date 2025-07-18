import { openDB, deleteDB } from 'idb';

const DB_NAME = 'camera-cache';
const STORE = 'photos';
const KEY = 'pendingPhoto';

export interface UiSnapshot {
  prompt?: string;
  selectedWorkflow?: string;
  selectedRefiner?: string;
  selectedPublish?: string;
  workflowParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  referenceUrls?: string[];
  timestamp?: number;
}

// 1. Store photo to IndexedDB and set sessionStorage flag
export async function putPhoto(blob: Blob): Promise<void> {
  console.log('photoCache: Storing photo to IndexedDB');
  try {
    const db = await openDB(DB_NAME, 1, {
      upgrade(db) { 
        db.createObjectStore(STORE); 
      }
    });
    
    await db.put(STORE, blob, KEY);
    
    // Set flag with timestamp
    sessionStorage.setItem('photoKey', KEY);
    sessionStorage.setItem('photoTimestamp', Date.now().toString());
    console.log('photoCache: Photo stored successfully');
  } catch (error) {
    console.error('photoCache: Error storing photo:', error);
  }
}

// 2. On refresh: restore photo if < 1 minute old
export async function restorePhotoIfValid(): Promise<Blob | null> {
  const startTime = performance.now();
  console.log('photoCache: Checking for cached photo');
  showDebugMessage('📸 Cache: Starting photo check');
  
  const photoKey = sessionStorage.getItem('photoKey');
  const timestampStr = sessionStorage.getItem('photoTimestamp');
  
  if (!photoKey || !timestampStr) {
    console.log('photoCache: No cached photo found');
    showDebugMessage('📸 Cache: No cached photo found');
    return null;
  }
  
  // Check if < 15 seconds old
  const timestamp = parseInt(timestampStr);
  const age = Date.now() - timestamp;
  const FIFTEEN_SECONDS = 15000;
  
  if (age > FIFTEEN_SECONDS) {
    console.log('photoCache: Cached photo too old, clearing');
    showDebugMessage('📸 Cache: Photo too old, clearing');
    await clearAll();
    return null;
  }
  
  console.log('photoCache: Cached photo is valid, restoring');
  showDebugMessage('📸 Cache: Photo valid, opening IndexedDB...');
  
  try {
    const dbStartTime = performance.now();
    const db = await openDB(DB_NAME, 1);
    const dbTime = performance.now() - dbStartTime;
    console.log(`photoCache: IndexedDB opened in ${dbTime.toFixed(2)}ms`);
    showDebugMessage(`📸 Cache: DB opened in ${dbTime.toFixed(0)}ms`);
    
    const getStartTime = performance.now();
    const blob = await db.get(STORE, KEY);
    const getTime = performance.now() - getStartTime;
    console.log(`photoCache: Photo retrieved in ${getTime.toFixed(2)}ms`);
    showDebugMessage(`📸 Cache: Photo retrieved in ${getTime.toFixed(0)}ms`);
    
    if (blob) {
      const totalTime = performance.now() - startTime;
      console.log(`photoCache: Total restore time: ${totalTime.toFixed(2)}ms`);
      showDebugMessage(`📸 Cache: Total restore time: ${totalTime.toFixed(0)}ms`);
      
      // Clear cache after successful restore
      await clearAll();
      return blob as Blob;
    }
  } catch (error) {
    console.error('photoCache: Error restoring photo:', error);
    showDebugMessage('❌ Cache: Error restoring photo');
    await clearAll();
  }
  
  return null;
}

// Save form state
export function saveFormState(formState: UiSnapshot): void {
  console.log('photoCache: Saving form state');
  console.log('photoCache: Form state to save:', formState);
  showDebugMessage('📸 Cache: Saving form state');
  
  const stateWithTimestamp = {
    ...formState,
    timestamp: Date.now()
  };
  sessionStorage.setItem('formState', JSON.stringify(stateWithTimestamp));
  console.log('photoCache: Form state saved to sessionStorage');
  showDebugMessage('📸 Cache: Form state saved to sessionStorage');
}

// Restore form state if valid
export function restoreFormState(): UiSnapshot | null {
  console.log('photoCache: Checking for cached form state');
  showDebugMessage('📸 Cache: Checking for form state');
  
  const stored = sessionStorage.getItem('formState');
  if (!stored) {
    console.log('photoCache: No form state found in sessionStorage');
    showDebugMessage('📸 Cache: No form state found');
    return null;
  }
  
  try {
    const state = JSON.parse(stored) as UiSnapshot;
    console.log('photoCache: Parsed form state from sessionStorage:', state);
    showDebugMessage('📸 Cache: Form state parsed from sessionStorage');
    
    // Check if < 15 seconds old
    if (state.timestamp && (Date.now() - state.timestamp) < 15000) {
      console.log('photoCache: Form state is valid, restoring');
      showDebugMessage('📸 Cache: Form state is valid, restoring');
      return state;
    } else {
      console.log('photoCache: Form state too old, clearing');
      showDebugMessage('📸 Cache: Form state too old, clearing');
      sessionStorage.removeItem('formState');
    }
  } catch (error) {
    console.error('photoCache: Error parsing form state:', error);
    showDebugMessage('❌ Cache: Error parsing form state');
    sessionStorage.removeItem('formState');
  }
  
  return null;
}

// 3. Clear everything
export async function clearAll(): Promise<void> {
  console.log('photoCache: Clearing all cached data');
  
  // Clear sessionStorage
  sessionStorage.removeItem('photoKey');
  sessionStorage.removeItem('photoTimestamp');
  sessionStorage.removeItem('formState');
  
  // Clear IndexedDB
  try {
    const db = await openDB(DB_NAME, 1);
    await db.delete(STORE, KEY);
  } catch (error) {
    // Continue silently
  }
} 

// TEMPORARY: Debug message display function
const showDebugMessage = (message: string) => {
  // Create or update debug element
  let debugEl = document.getElementById('debug-messages');
  if (!debugEl) {
    debugEl = document.createElement('div');
    debugEl.id = 'debug-messages';
    debugEl.style.cssText = `
      position: fixed;
      top: 10px;
      right: 10px;
      background: rgba(0,0,0,0.8);
      color: white;
      padding: 10px;
      border-radius: 5px;
      font-family: monospace;
      font-size: 12px;
      z-index: 10000;
      max-width: 300px;
      word-wrap: break-word;
    `;
    document.body.appendChild(debugEl);
  }
  
  const timestamp = new Date().toLocaleTimeString();
  debugEl.innerHTML += `<div>[${timestamp}] ${message}</div>`;
  
  // Keep only last 10 messages
  const messages = debugEl.children;
  if (messages.length > 10) {
    debugEl.removeChild(messages[0]);
  }
  
  // Auto-clear after 15 seconds (3x longer)
  setTimeout(() => {
    if (debugEl && debugEl.children.length > 0) {
      debugEl.removeChild(debugEl.children[0]);
    }
  }, 15000);
}; 