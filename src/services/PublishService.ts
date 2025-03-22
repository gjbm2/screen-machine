
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
      // First try Web Share API
      if (navigator.share) {
        try {
          await navigator.share({
            title: 'Share generated image',
            text: generationInfo?.prompt || 'Check out this generated image!',
            url: imageUrl,
          });
          toast.success('Shared successfully');
          return true;
        } catch (error) {
          console.error('Error sharing:', error);
          
          // If share fails or is denied, fall back to clipboard
          if (error instanceof Error && error.name !== 'AbortError') {
            await fallbackToClipboard(imageUrl);
          }
          return true;
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
