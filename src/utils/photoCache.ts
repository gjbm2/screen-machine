import { openDB, deleteDB } from 'idb';

const DB_NAME = 'camera-cache';
const STORE = 'photos';
const KEY = 'pendingPhoto';

export interface UiSnapshot {
  showCamera?: boolean;
  facingMode?: 'user' | 'environment';
  timestamp?: number;
  sessionId?: string;
  // Complete form state
  prompt?: string;
  selectedWorkflow?: string;
  selectedRefiner?: string;
  selectedPublish?: string;
  workflowParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  referenceUrls?: string[];
  isWaitingForCamera?: boolean;
}

export async function putPhoto(blob: Blob): Promise<void> {
  console.log('photoCache: putPhoto called with blob size:', blob.size);
  try {
    console.log('photoCache: Opening IndexedDB for write...');
    const db = await openDB(DB_NAME, 1, {
      upgrade(db, oldVersion, newVersion) { 
        console.log('photoCache: Upgrading IndexedDB from', oldVersion, 'to', newVersion);
        if (oldVersion < 1) {
          console.log('photoCache: Creating object store');
          db.createObjectStore(STORE); 
        }
      }
    });
    console.log('photoCache: IndexedDB opened for write');
    
    await db.put(STORE, blob, KEY);
    console.log('photoCache: Blob stored in IndexedDB');
    
    sessionStorage.setItem('photoKey', KEY);
    console.log('photoCache: photoKey set in sessionStorage');
  } catch (error) {
    console.error('photoCache: Error in putPhoto:', error);
    // Auto-reset and retry on error
    try {
      console.log('photoCache: Attempting auto-reset and retry...');
      await resetDatabase();
      const db = await openDB(DB_NAME, 1, {
        upgrade(db, oldVersion, newVersion) { 
          if (oldVersion < 1) {
            db.createObjectStore(STORE); 
          }
        }
      });
      await db.put(STORE, blob, KEY);
      sessionStorage.setItem('photoKey', KEY);
      console.log('photoCache: Photo stored after retry');
    } catch (retryError) {
      console.error('photoCache: Retry failed:', retryError);
      // If retry fails, just continue without IndexedDB
    }
  }
}

export async function takePhotoFromCache(): Promise<Blob | null> {
  console.log('photoCache: takePhotoFromCache called');
  const flag = sessionStorage.getItem('photoKey');
  console.log('photoCache: photoKey from sessionStorage:', flag);
  
  if (!flag) {
    console.log('photoCache: No photoKey found, returning null');
    return null;
  }

  try {
    console.log('photoCache: Opening IndexedDB...');
    const db = await openDB(DB_NAME, 1);
    console.log('photoCache: IndexedDB opened successfully');
    
    const blob = await db.get(STORE, flag);
    console.log('photoCache: Retrieved blob from IndexedDB:', !!blob);
    
    if (blob) {
      console.log('photoCache: Blob found, cleaning up...');
      await db.delete(STORE, flag);
      sessionStorage.removeItem('photoKey');
      console.log('photoCache: Cleanup completed, returning blob');
      return blob as Blob;
    } else {
      console.log('photoCache: No blob found in IndexedDB');
    }
  } catch (error) {
    console.error('photoCache: Error retrieving from cache:', error);
    // Clean up corrupted state
    try {
      await resetDatabase();
    } catch (cleanupError) {
      console.error('photoCache: Error during cleanup:', cleanupError);
    }
  }
  return null;
}

export function saveUiSnapshot(snapshot: UiSnapshot): void {
  console.log('photoCache: saveUiSnapshot called with:', snapshot);
  
  const snapshotWithTimestamp = {
    ...snapshot,
    timestamp: Date.now()
  };
  
  console.log('photoCache: Storing in sessionStorage:', snapshotWithTimestamp);
  sessionStorage.setItem('uiSnapshot', JSON.stringify(snapshotWithTimestamp));
  console.log('photoCache: uiSnapshot stored in sessionStorage');
}

export function saveCompleteFormState(formState: {
  prompt: string;
  selectedWorkflow: string;
  selectedRefiner: string;
  selectedPublish: string;
  workflowParams: Record<string, any>;
  refinerParams: Record<string, any>;
  globalParams: Record<string, any>;
  referenceUrls: string[];
}): void {
  console.log('photoCache: saveCompleteFormState called with:', formState);
  
  const snapshot: UiSnapshot = {
    ...formState,
    // Don't save isWaitingForCamera as it's transient UI state - only save for photo restoration
    facingMode: 'environment',
    timestamp: Date.now()
  };
  
  console.log('photoCache: Saving snapshot:', snapshot);
  saveUiSnapshot(snapshot);
}

export function saveCameraWaitingState(formState: {
  prompt: string;
  selectedWorkflow: string;
  selectedRefiner: string;
  selectedPublish: string;
  workflowParams: Record<string, any>;
  refinerParams: Record<string, any>;
  globalParams: Record<string, any>;
  referenceUrls: string[];
}): void {
  console.log('photoCache: saveCameraWaitingState called with:', formState);
  
  const snapshot: UiSnapshot = {
    ...formState,
    isWaitingForCamera: true,
    facingMode: 'environment',
    timestamp: Date.now()
  };
  
  console.log('photoCache: Saving camera waiting state:', snapshot);
  saveUiSnapshot(snapshot);
}

export function restoreFormState(): UiSnapshot | null {
  console.log('photoCache: restoreFormState called');
  const snapshot = loadUiSnapshot();
  console.log('photoCache: loadUiSnapshot result:', !!snapshot);
  
  if (snapshot) {
    console.log('photoCache: Snapshot found:', snapshot);
    console.log('photoCache: isWaitingForCamera:', snapshot.isWaitingForCamera);
    
    // Only return snapshots that are for camera waiting or have form data
    if (snapshot.isWaitingForCamera || snapshot.prompt || snapshot.selectedWorkflow) {
      console.log('photoCache: Returning snapshot for restoration');
      return snapshot;
    } else {
      console.log('photoCache: Snapshot exists but has no useful data');
    }
  }
  
  console.log('photoCache: No valid snapshot found for restoration');
  return null;
}

export function loadUiSnapshot(): UiSnapshot | null {
  console.log('photoCache: loadUiSnapshot called');
  const stored = sessionStorage.getItem('uiSnapshot');
  console.log('photoCache: uiSnapshot from sessionStorage:', stored);
  
  if (!stored) {
    console.log('photoCache: No uiSnapshot found');
    return null;
  }
  
  try {
    const snapshot = JSON.parse(stored) as UiSnapshot;
    console.log('photoCache: Parsed snapshot:', snapshot);
    
    // Only restore if timestamp is less than 5 minutes old
    if (snapshot.timestamp && (Date.now() - snapshot.timestamp) < 5 * 60 * 1000) {
      console.log('photoCache: Snapshot is valid (timestamp check passed)');
      return snapshot;
    } else {
      console.log('photoCache: Snapshot is too old, clearing...');
    }
  } catch (error) {
    console.error('photoCache: Error parsing snapshot:', error);
  }
  
  clearUiSnapshot();
  return null;
}

export function clearUiSnapshot(): void {
  sessionStorage.removeItem('uiSnapshot');
  sessionStorage.removeItem('photoKey');
}

export function clearRestorationState(): void {
  sessionStorage.removeItem('hasRestored');
  clearUiSnapshot();
}

export function hasAlreadyRestored(): boolean {
  const result = sessionStorage.getItem('hasRestored') === 'true';
  console.log('photoCache: hasAlreadyRestored result:', result);
  return result;
}

export function markAsRestored(): void {
  console.log('photoCache: markAsRestored called');
  sessionStorage.setItem('hasRestored', 'true');
}

export async function clearPhotoCache(): Promise<void> {
  try {
    const db = await openDB(DB_NAME, 1);
    await db.delete(STORE, KEY);
  } catch (error) {
    // Continue silently
  }
  clearUiSnapshot();
}

export async function resetDatabase(): Promise<void> {
  try {
    const db = await openDB(DB_NAME, 1);
    db.close();
    await deleteDB(DB_NAME);
  } catch (error) {
    // Continue silently
  }
  clearUiSnapshot();
} 