import React, { useRef, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Upload, Camera, Palette } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useIsMobile } from '@/hooks/use-mobile';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';
import { pickerClosed, pickerOpened, reportRestored, takeClosedPage, watchForm } from '@/utils/photoCache';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuPortal,
} from "@/components/ui/dropdown-menu";
import { ArtworkPicker } from '@/components/artworks/ArtworkPicker';

// Debug flag - set to true to enable on-screen debug messages
const DEBUG_MODE = false;


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
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const cameraInputRef = useRef<HTMLInputElement | null>(null);
  // a picker closed without a photo fires 'cancel' on its input (React has no prop for it on an input)
  const pickerInput = (ref: React.MutableRefObject<HTMLInputElement | null>) => (node: HTMLInputElement | null) => {
    ref.current = node;
    node?.addEventListener('cancel', pickerClosed);
  };
  const buttonRef = useRef<HTMLButtonElement>(null);
  const isMobile = useIsMobile();
  const [menuOpen, setMenuOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  // Famous artworks (components/artworks): picked pictures arrive as files, like an upload
  const [artOpen, setArtOpen] = useState(false);
  const openArtworks = () => {
    setMenuOpen(false);
    setTimeout(() => setArtOpen(true), 50);   // after the menu has closed, so it doesn't take the focus back
  };
  const artworkPicker = <ArtworkPicker open={artOpen} onOpenChange={setArtOpen} onPick={onImageUpload} />;
  
  const { addReferenceUrl, clearReferenceUrls } = useReferenceImages();

  const isLikelyImageByName = (name: string) => {
    const lower = name.toLowerCase();
    return (
      lower.endsWith('.jpg') ||
      lower.endsWith('.jpeg') ||
      lower.endsWith('.png') ||
      lower.endsWith('.webp') ||
      lower.endsWith('.gif') ||
      lower.endsWith('.avif') ||
      lower.endsWith('.heic') ||
      lower.endsWith('.heif')
    );
  };

  const validateIncomingImageFile = (file: File): string | null => {
    // Some mobile browsers return empty mime types; fall back to filename heuristics.
    const typeOk = file.type ? file.type.startsWith('image/') : isLikelyImageByName(file.name);
    if (!typeOk) return `${file.name || 'File'} is not an image file`;

    // A camera original (often 5-12 MB) is welcome: the server re-encodes every upload to a JPEG of at most 5 MB
    // (routes/utils.encode_image_uploads); this only stops something absurd
    const MAX_BYTES = 30 * 1024 * 1024;
    if (file.size > MAX_BYTES) return `${file.name || 'Image'} is over 30 MB`;

    return null;
  };

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

  // The form is kept while the page is hidden, for the browser closing the page (utils/photoCache.ts)
  const formState = useRef(currentFormState);
  formState.current = currentFormState;
  useEffect(() => watchForm(() => formState.current || {}), []);

  // A page the browser closed (most often while the camera or the picker was open) comes back with its form
  const hasCheckedCache = useRef(false);
  useEffect(() => {
    if (hasCheckedCache.current) return;
    hasCheckedCache.current = true;
    (async () => {
      const closed = await takeClosedPage();
      if (!closed) return;
      const { page, photos } = closed;
      if (photos.length > 0) onImageUpload(photos);
      if (onRestoreFormState) onRestoreFormState(page.form);
      reportRestored(page, photos.length);

      const hadPrompt = !!(page.form.prompt || '').trim();
      const lostPhotos = page.photos - photos.length;
      let back = '';
      if (hadPrompt && photos.length > 0) back = photos.length > 1 ? 'Your prompt and photos are back.' : 'Your prompt and photo are back.';
      else if (photos.length > 0) back = photos.length > 1 ? 'Your photos are back.' : 'Your photo is back.';
      else if (hadPrompt) back = 'Your prompt is back.';
      const again = page.picking ? 'Choose the photo again.' : lostPhotos > 0 ? 'Add the photo again.' : '';
      const what = page.picking
        ? 'Your browser closed this page while you were choosing a photo.'
        : 'Your browser had closed this page.';
      toast.message([what, back, again].filter(Boolean).join(' '), { duration: 10000 });
    })();
  }, []);

  const processFiles = (files: FileList | null) => {
    try {
      if (!files || files.length === 0) return;

      const validFiles: File[] = [];
      Array.from(files).forEach(file => {
        // one check for every way in (a HEIC from a phone can come with no type: its name says)
        const problem = validateIncomingImageFile(file);
        if (problem) {
          toast.error(problem);
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
    pickerClosed();
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

  const triggerFileInput = () => {
    pickerOpened('gallery');
    fileInputRef.current?.click();
  };
  
  const triggerCameraInput = () => {
    setMenuOpen(false);
    pickerOpened('camera');
    setTimeout(() => {
      cameraInputRef.current?.click();
    }, 100);
  };

  // A camera photo comes in like any other; the form keeps it from then on (PromptForm, utils/photoCache.ts)
  const handleCameraInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    pickerClosed();
    processFiles(e.target.files);
    // cleared so that taking the same photo again still fires onChange on iOS/Safari
    e.target.value = '';
  };

  const dragButtonClass = `${isDragging ? 'bg-purple-100 border-purple-500' : 'border border-input'} transition-colors`;

  // TEMPORARY: Debug message display function
  const showDebugMessage = (message: string) => {
    if (!DEBUG_MODE) return; // Only show debug messages if DEBUG_MODE is true
    
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
          ref={pickerInput(fileInputRef)}
          className="hidden"
          accept="image/*"
          onChange={handleImageUpload}
          disabled={isLoading}
          multiple
        />
        <input
          type="file"
          ref={pickerInput(cameraInputRef)}
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
              <DropdownMenuItem onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                openArtworks();
              }} className="px-4 py-1.5 text-sm">
                <Palette className="h-4 w-4 mr-2" />
                Famous artworks
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenuPortal>
        </DropdownMenu>
        {artworkPicker}
      </>
    );
  }

  return (
    <>
      <input
        type="file"
        ref={pickerInput(fileInputRef)}
        className="hidden"
        accept="image/*"
        onChange={handleImageUpload}
        disabled={isLoading}
        multiple
      />

      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
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
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56 p-1">
          <DropdownMenuItem onClick={triggerFileInput} className="px-3 py-1.5 text-sm">
            <Upload className="h-4 w-4 mr-2" />
            From this computer
          </DropdownMenuItem>
          <DropdownMenuItem onClick={openArtworks} className="px-3 py-1.5 text-sm">
            <Palette className="h-4 w-4 mr-2" />
            Famous artworks
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {artworkPicker}
    </>
  );
};

export default ImageUploader;
