
import { toast } from 'sonner';
import publishDestinations from '@/data/publish-destinations.json';

export interface PublishDestination {
  id: string;
  name: string;
  icon: string;
  type: string;
  description: string;
  file?: string;
}

export const getPublishDestinations = (): PublishDestination[] => {
  return publishDestinations;
};

export const publishImage = async (
  imageUrl: string, 
  destinationId: string, 
  generationInfo?: { 
    prompt?: string;
    workflow?: string;
    params?: Record<string, any>;
  }
): Promise<boolean> => {
  const destination = publishDestinations.find(dest => dest.id === destinationId);
  
  if (!destination) {
    toast.error(`Unknown destination: ${destinationId}`);
    return false;
  }

  // Handle device sharing (handled by frontend)
  if (destination.type === 'share') {
    try {
      // Check if Web Share API is available
      if (navigator.share) {
        // For Web Share API Level 2 support - get image as blob and share as file
        try {
          const response = await fetch(imageUrl);
          
          if (!response.ok) {
            throw new Error(`Failed to fetch image: ${response.status} ${response.statusText}`);
          }
          
          const imageBlob = await response.blob();
          
          // Create a File object from the Blob
          // Extract filename from URL or use a default name with timestamp
          const filename = imageUrl.split('/').pop() || `generated-image-${Date.now()}.png`;
          const imageFile = new File([imageBlob], filename, { type: imageBlob.type });
          
          // Share object with title, text, URL and files array
          const shareData: ShareData = {
            title: 'Share generated image',
            text: generationInfo?.prompt || 'Check out this generated image!',
            files: [imageFile]
          };
          
          // Check if files sharing is supported (Web Share API Level 2)
          if ('canShare' in navigator && navigator.canShare && navigator.canShare({ files: [imageFile] })) {
            await navigator.share(shareData);
            toast.success('Shared successfully');
            return true;
          } else {
            // Fall back to Web Share API Level 1 (URL only)
            await navigator.share({
              title: 'Share generated image',
              text: generationInfo?.prompt || 'Check out this generated image!',
              url: imageUrl,
            });
            toast.success('Shared successfully');
            return true;
          }
        } catch (error) {
          console.error('Error sharing:', error);
          
          // If share fails or is denied, fall back to clipboard
          if (error instanceof Error && error.name !== 'AbortError') {
            return await fallbackToClipboard(imageUrl);
          }
          
          return false;
        }
      } else {
        // Fallback to clipboard if Web Share API not available
        return await fallbackToClipboard(imageUrl);
      }
    } catch (error) {
      console.error('Error sharing:', error);
      toast.error('Failed to share image');
      return false;
    }
  }

  // Handle backend publishing
  try {
    const response = await fetch('/api/publish-image', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        imageUrl,
        destination: destination.id,
        destinationType: destination.type,
        destinationFile: destination.file,
        metadata: {
          prompt: generationInfo?.prompt,
          workflow: generationInfo?.workflow,
          params: generationInfo?.params,
        }
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: 'Failed to publish image' }));
      throw new Error(errorData.error || 'Failed to publish image');
    }

    const result = await response.json().catch(() => ({ success: true }));
    toast.success(`Published to ${destination.name}`);
    return true;
  } catch (error) {
    console.error('Error publishing image:', error);
    toast.error(`Failed to publish to ${destination.name}`);
    return false;
  }
};

// Helper function for clipboard fallback
const fallbackToClipboard = async (imageUrl: string): Promise<boolean> => {
  try {
    await navigator.clipboard.writeText(imageUrl);
    toast.success('Image URL copied to clipboard');
    return true;
  } catch (clipError) {
    console.error('Clipboard fallback failed:', clipError);
    toast.error('Failed to copy image URL');
    return false;
  }
};
