import React, { useRef, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Upload, Camera } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useIsMobile } from '@/hooks/use-mobile';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';
import { 
  putPhoto, 
  restorePhotoIfValid,
  saveFormState,
  restoreFormState,
  clearAll
} from '@/utils/photoCache';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuPortal,
} from "@/components/ui/dropdown-menu";


interface ImageUploaderProps {
  isLoading: boolean;
  onImageUpload: (files: File[]) => void;
  hideLabel?: boolean;
  // Complete form state for session restoration
  currentFormState?: {
    prompt: string;
    selectedWorkflow: string;
    selectedRefiner: string;
    selectedPublish: string;
    workflowParams: Record<string, any>;
    refinerParams: Record<string, any>;
    globalParams: Record<string, any>;
    referenceUrls: string[];
  };
  onRestoreFormState?: (formState: any) => void;
}



const ImageUploader: React.FC<ImageUploaderProps> = ({
  isLoading,
  onImageUpload,
  hideLabel = false,
  currentFormState,
  onRestoreFormState
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const isMobile = useIsMobile();
  const [menuOpen, setMenuOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  
  const { addReferenceUrl, clearReferenceUrls } = useReferenceImages();

  useEffect(() => {
    const handleImageSelected = (event: CustomEvent) => {
      if (event.detail && event.detail.files) {
        onImageUpload(event.detail.files);
      }
    };

    document.addEventListener('image-selected', handleImageSelected as EventListener);
    return () => {
      document.removeEventListener('image-selected', handleImageSelected as EventListener);
    };
  }, [onImageUpload]);

  // Check for cached photo on mount (only once)
  const hasCheckedCache = useRef(false);
  useEffect(() => {
    if (hasCheckedCache.current) {
      return; // Already checked, don't check again
    }
    
    const restoreFromCache = async () => {
      const startTime = performance.now();
      console.log('ImageUploader: Checking for cached photo on mount');
      showDebugMessage('📸 Checking for cached photo');
      
      // IMPORTANT: Get form state BEFORE calling restorePhotoIfValid which clears the cache
      const formStateStartTime = performance.now();
      const formState = restoreFormState();
      const formStateTime = performance.now() - formStateStartTime;
      console.log(`ImageUploader: restoreFormState took ${formStateTime.toFixed(2)}ms`);
      
      const cachedPhoto = await restorePhotoIfValid();
      
      if (cachedPhoto) {
        console.log('ImageUploader: Found cached photo, restoring');
        showDebugMessage('📸 Found cached photo, converting to data URI...');
        
        // Convert to data URI and display
        const readerStartTime = performance.now();
        const reader = new FileReader();
        reader.onload = (e) => {
          const readerTime = performance.now() - readerStartTime;
          const dataUri = e.target?.result as string;
          console.log('ImageUploader: Adding cached photo to display');
          console.log(`ImageUploader: FileReader conversion took ${readerTime.toFixed(2)}ms`);
          showDebugMessage(`📸 Cached photo displayed (conversion: ${readerTime.toFixed(0)}ms)`);
          
          // Add timing for addReferenceUrl
          const addRefStartTime = performance.now();
          addReferenceUrl(dataUri, false);
          const addRefTime = performance.now() - addRefStartTime;
          console.log(`ImageUploader: addReferenceUrl took ${addRefTime.toFixed(2)}ms`);
          showDebugMessage(`📸 Reference URL added in ${addRefTime.toFixed(0)}ms`);
        };
        reader.readAsDataURL(cachedPhoto);
        
        // Restore form state if available (already retrieved above)
        if (formState && onRestoreFormState) {
          console.log('ImageUploader: Restoring form state');
          showDebugMessage('📸 Form state restored');
          
          const onRestoreStartTime = performance.now();
          onRestoreFormState(formState);
          const onRestoreTime = performance.now() - onRestoreStartTime;
          console.log(`ImageUploader: onRestoreFormState callback took ${onRestoreTime.toFixed(2)}ms`);
          showDebugMessage(`📸 Form state callback: ${onRestoreTime.toFixed(0)}ms`);
        }
        
        const totalTime = performance.now() - startTime;
        console.log(`ImageUploader: Total cache restoration took ${totalTime.toFixed(2)}ms`);
        showDebugMessage(`📸 Total cache restoration: ${totalTime.toFixed(0)}ms`);
      } else {
        console.log('ImageUploader: No cached photo found');
        showDebugMessage('📸 No cached photo found');
      }
    };

    hasCheckedCache.current = true;
    restoreFromCache();
  }, []); // Empty dependency array - only run once on mount

  const processFiles = (files: FileList | null) => {
    try {
      if (!files || files.length === 0) return;

      const validFiles: File[] = [];
      Array.from(files).forEach(file => {
        if (!file.type.startsWith('image/')) {
          toast.error(`${file.name} is not an image file`);
          return;
        }
        if (file.size > 5 * 1024 * 1024) {
          toast.error(`${file.name} exceeds the 5MB size limit`);
          return;
        }
        validFiles.push(file);
      });

      if (validFiles.length > 0) {
        // TEMPORARY: Debug message
        console.log('📸 UPLOADER: Processing files', validFiles.length);
        showDebugMessage(`📸 Processing ${validFiles.length} files`);
        
        // Add a small delay on mobile to prevent race conditions
        if (isMobile) {
          setTimeout(() => {
            onImageUpload(validFiles);
            showDebugMessage('📸 Files uploaded (mobile)');
          }, 50);
        } else {
          onImageUpload(validFiles);
          showDebugMessage('📸 Files uploaded');
        }
        // Let the backend handle workflow selection
      }
    } catch (error) {
      console.error('Error processing files:', error);
      showDebugMessage('❌ File processing failed');
      toast.error('Error processing uploaded files');
    }
  };

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    e.stopPropagation();
    processFiles(e.target.files);
    e.target.value = '';
  };

  const processURL = async (url: string) => {
    try {
      const isValidURL = /^(https?:\/\/)/i.test(url);
      if (!isValidURL) {
        toast.error("Invalid URL format");
        return;
      }

      const response = await fetch(url, { method: 'HEAD' });
      
      if (!response.ok) {
        toast.error("Could not access the URL");
        return;
      }
      
      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.startsWith('image/')) {
        toast.error("URL does not point to a valid image");
        return;
      }
      
      const imageResponse = await fetch(url);
      const blob = await imageResponse.blob();
      const fileName = url.split('/').pop() || 'image.jpg';
      const file = new File([blob], fileName, { type: blob.type });
      
      onImageUpload([file]);
      
      // Let the backend handle workflow selection
    } catch (error) {
      console.error('Error processing URL:', error);
      toast.error("Failed to process image URL");
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isLoading) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isLoading) {
      setIsDragging(true);
      e.dataTransfer.dropEffect = 'copy';
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    
    if (isLoading) return;
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processFiles(e.dataTransfer.files);
      return;
    }
    
    const url = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text');
    if (url) {
      processURL(url);
    }
  };

  const triggerFileInput = () => fileInputRef.current?.click();
  
  const triggerCameraInput = () => {
    setMenuOpen(false);
    setTimeout(() => {
      cameraInputRef.current?.click();
    }, 100);
  };

  const handleCameraInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    console.log('ImageUploader: handleCameraInput called');
    const file = e.target.files?.[0];
    if (!file) {
      console.log('ImageUploader: No file selected');
      return;
    }

    // TEMPORARY: Debug message
    console.log('📸 UPLOADER: Camera input received', file.name, file.size);
    showDebugMessage('📸 Camera photo received');

    console.log('ImageUploader: Processing camera photo');

    try {
      // 1. Store photo to cache
      await putPhoto(file);
      console.log('ImageUploader: Photo stored to cache');
      showDebugMessage('📸 Photo cached');

      // 2. Save current form state
      if (currentFormState) {
        console.log('ImageUploader: currentFormState provided:', currentFormState);
        showDebugMessage('📸 Form state provided, saving...');
        saveFormState(currentFormState);
        console.log('ImageUploader: Form state saved');
        showDebugMessage('📸 Form state saved');
      } else {
        console.log('ImageUploader: No currentFormState provided!');
        showDebugMessage('❌ No form state provided');
      }

      // 3. Convert to data URI and display
      const reader = new FileReader();
      reader.onload = (e) => {
        const dataUri = e.target?.result as string;
        console.log('ImageUploader: Adding photo to display');
        showDebugMessage('📸 Photo displayed');
        addReferenceUrl(dataUri, false);
      };
      reader.readAsDataURL(file);

      // Clear input
      e.target.value = '';

    } catch (error) {
      console.error('ImageUploader: Error processing camera input:', error);
      showDebugMessage('❌ Camera input failed');
      toast.error('Failed to process photo');
    }
  };

  const dragButtonClass = `${isDragging ? 'bg-purple-100 border-purple-500' : 'border border-input'} transition-colors`;

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

  if (isMobile) {
    return (
      <>
        <input
          type="file"
          ref={fileInputRef}
          className="hidden"
          accept="image/*"
          onChange={handleImageUpload}
          disabled={isLoading}
          multiple
        />
        <input
          type="file"
          ref={cameraInputRef}
          className="hidden"
          accept="image/*"
          onChange={handleCameraInput}
          disabled={isLoading}
          capture="environment"
          onClick={(e) => e.stopPropagation()}
          onFocus={(e) => e.stopPropagation()}
        />

        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="outline"
              className={`h-[36px] rounded-md flex-shrink-0 text-sm flex items-center gap-2 px-3 ${dragButtonClass} ${
                menuOpen
                  ? 'bg-purple-500/10 text-purple-700'
                  : 'hover:bg-purple-500/10 text-purple-700'
              }`}
              disabled={isLoading}
              ref={buttonRef}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
            >
              <Upload className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuPortal>
            <DropdownMenuContent align="start" className="w-64 p-2">
              <div className="text-sm font-semibold mb-1 px-2">Upload options</div>
              <DropdownMenuItem onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                triggerFileInput();
              }} className="px-4 py-1.5 text-sm">
                <Upload className="h-4 w-4 mr-2" />
                From Gallery
              </DropdownMenuItem>
              <DropdownMenuItem onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                triggerCameraInput();
              }} className="px-4 py-1.5 text-sm">
                <Camera className="h-4 w-4 mr-2" />
                From Camera
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenuPortal>
        </DropdownMenu>
      </>
    );
  }

  return (
    <>
      <input
        type="file"
        ref={fileInputRef}
        className="hidden"
        accept="image/*"
        onChange={handleImageUpload}
        disabled={isLoading}
        multiple
      />

      <Button
        type="button"
        variant="outline"
        onClick={triggerFileInput}
        className={`h-[36px] rounded-md flex-shrink-0 text-sm flex items-center gap-2 px-3 hover:bg-purple-500/10 text-purple-700 ${dragButtonClass}`}
        disabled={isLoading}
        ref={buttonRef}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        <Upload className="h-4 w-4" />
        {!hideLabel && "Upload"}
      </Button>
    </>
  );
};

export default ImageUploader;
