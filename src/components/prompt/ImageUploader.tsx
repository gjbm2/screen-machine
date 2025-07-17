import React, { useRef, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Upload, Camera } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useIsMobile } from '@/hooks/use-mobile';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';
import { 
  putPhoto, 
  takePhotoFromCache, 
  saveCompleteFormState, 
  saveCameraWaitingState,
  restoreFormState,
  loadUiSnapshot, 
  clearUiSnapshot,
  clearPhotoCache,
  hasAlreadyRestored,
  markAsRestored,
  clearRestorationState
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
  const [isWaitingForCamera, setIsWaitingForCamera] = useState(false);
  
  const { addReferenceUrl } = useReferenceImages();

  useEffect(() => {
    const handleImageSelected = (event: CustomEvent) => {
      if (event.detail && event.detail.files) {
        onImageUpload(event.detail.files);
        // Let the backend handle workflow selection
      }
    };

    document.addEventListener('image-selected', handleImageSelected as EventListener);
    return () => {
      document.removeEventListener('image-selected', handleImageSelected as EventListener);
    };
  }, [onImageUpload]);

  // Check for cached photos on component mount
  useEffect(() => {
    const restoreFromCache = async () => {
      try {
        console.log('ImageUploader: Starting cache restoration...');
        console.log('ImageUploader: isMobile:', isMobile);
        console.log('ImageUploader: sessionStorage contents:', {
          hasRestored: sessionStorage.getItem('hasRestored'),
          photoKey: sessionStorage.getItem('photoKey'),
          uiSnapshot: sessionStorage.getItem('uiSnapshot')
        });
        
        // Clear the restoration flag on page load to allow restoration to happen
        // This way restoration can happen on every page load until user completes their action
        console.log('ImageUploader: Clearing hasRestored flag to allow restoration');
        sessionStorage.removeItem('hasRestored');
        
        console.log('ImageUploader: Calling takePhotoFromCache()...');
        const cachedPhoto = await takePhotoFromCache();
        console.log('ImageUploader: takePhotoFromCache result:', !!cachedPhoto);
        
        console.log('ImageUploader: Calling restoreFormState()...');
        const formState = restoreFormState();
        console.log('ImageUploader: restoreFormState result:', !!formState, formState);

        console.log('ImageUploader: Cache check results:', {
          hasCachedPhoto: !!cachedPhoto,
          hasFormState: !!formState,
          onRestoreFormState: !!onRestoreFormState
        });

        if (cachedPhoto) {
          console.log('ImageUploader: Found cached photo, processing...');
          // Photo exists - add to reference images automatically
          const reader = new FileReader();
          reader.onload = (e) => {
            try {
              const dataUri = e.target?.result as string;
              console.log('ImageUploader: FileReader completed, data URI length:', dataUri.length);
              console.log('ImageUploader: Adding cached photo to reference images');
              addReferenceUrl(dataUri, false);
              
              // Restore form state if available
              if (formState && onRestoreFormState) {
                console.log('ImageUploader: Calling onRestoreFormState with:', formState);
                onRestoreFormState(formState);
              }
              
              // Mark as restored and clean up
              markAsRestored();
              clearUiSnapshot();
              console.log('ImageUploader: Cache restoration completed');
            } catch (error) {
              console.error('ImageUploader: Error in FileReader onload:', error);
              markAsRestored();
              clearUiSnapshot();
            }
          };
          
          reader.onerror = (error) => {
            console.error('ImageUploader: FileReader error during restoration:', error);
            markAsRestored();
            clearUiSnapshot();
          };
          
          reader.readAsDataURL(cachedPhoto);
        } else if (formState) {
          console.log('ImageUploader: Found form state but no photo');
          
          // Check if this was a camera waiting state
          if (formState.isWaitingForCamera) {
            console.log('ImageUploader: User was waiting for camera - setting waiting state');
            setIsWaitingForCamera(true);
            
            // Set a timeout to clear the waiting state if camera is not used within 30 seconds
            setTimeout(() => {
              console.log('ImageUploader: Camera waiting timeout - clearing waiting state');
              setIsWaitingForCamera(false);
              clearUiSnapshot();
            }, 30000);
          } else {
            console.log('ImageUploader: Regular form state - not setting waiting state');
          }
          
          // Restore form state
          if (onRestoreFormState) {
            console.log('ImageUploader: Calling onRestoreFormState (no photo) with:', formState);
            onRestoreFormState(formState);
          }
          
          // Mark as restored
          markAsRestored();
        } else {
          console.log('ImageUploader: No cached data found');
          // No cached data - mark as checked to prevent future checks
          markAsRestored();
        }
      } catch (error) {
        console.error('ImageUploader: Error during cache restoration:', error);
        markAsRestored();
        clearUiSnapshot();
      }
    };

    restoreFromCache();
  }, [addReferenceUrl, onRestoreFormState, isMobile]);

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
        // Add a small delay on mobile to prevent race conditions
        if (isMobile) {
          setTimeout(() => {
            onImageUpload(validFiles);
          }, 50);
        } else {
          onImageUpload(validFiles);
        }
        // Let the backend handle workflow selection
      }
    } catch (error) {
      console.error('Error processing files:', error);
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
    // Save camera waiting state before opening camera
    if (currentFormState) {
      console.log('ImageUploader: Saving camera waiting state before camera...');
      saveCameraWaitingState(currentFormState);
    } else {
      console.log('ImageUploader: No currentFormState provided!');
    }
    
    // Close dropdown menu if open
    setMenuOpen(false);
    
    setIsWaitingForCamera(true);
    
    // Small delay to ensure menu closes before triggering camera
    setTimeout(() => {
      console.log('ImageUploader: Triggering camera input click...');
      cameraInputRef.current?.click();
    }, 100);
  };

  const handleCameraInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    console.log('ImageUploader: handleCameraInput called');
    const file = e.target.files?.[0];
    if (!file) {
      console.log('ImageUploader: No file selected from camera input');
      setIsWaitingForCamera(false);
      clearUiSnapshot();
      return;
    }

    console.log('ImageUploader: Camera input received file:', file.name, file.size, file.type);

    try {
      // 1. IMMEDIATELY save to IndexedDB FIRST (before any other operations)
      console.log('ImageUploader: Saving to IndexedDB...');
      await putPhoto(file);
      console.log('ImageUploader: Saved to IndexedDB successfully');

      // 2. Convert to data URI and add to reference images
      console.log('ImageUploader: Converting file to data URI...');
      const reader = new FileReader();
      reader.onload = (e) => {
        const dataUri = e.target?.result as string;
        console.log('ImageUploader: FileReader completed, data URI length:', dataUri.length);
        console.log('ImageUploader: Adding to reference images...');
        
        addReferenceUrl(dataUri, false);
        
        console.log('ImageUploader: Photo added to reference images successfully');
        setIsWaitingForCamera(false);
        // Clear the waiting state from sessionStorage to prevent it from persisting across refreshes
        clearUiSnapshot();
        // Also clear the restoration flag to prevent conflicts
        sessionStorage.removeItem('hasRestored');
      };
      
      reader.onerror = (error) => {
        console.error('ImageUploader: FileReader error:', error);
        setIsWaitingForCamera(false);
        clearUiSnapshot();
        toast.error('Failed to process photo');
      };
      
      reader.readAsDataURL(file);

      // 3. Clear the input
      e.target.value = '';

    } catch (error) {
      console.error('ImageUploader: Error processing camera input:', error);
      setIsWaitingForCamera(false);
      clearUiSnapshot();
      toast.error('Failed to process photo');
    }
  };

  const dragButtonClass = `${isDragging ? 'bg-purple-100 border-purple-500' : 'border border-input'} transition-colors`;

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
