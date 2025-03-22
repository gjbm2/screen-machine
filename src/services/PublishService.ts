
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
      if (navigator.share) {
        await navigator.share({
          title: 'Share generated image',
          text: generationInfo?.prompt || 'Check out this generated image!',
          url: imageUrl,
        });
        toast.success('Shared successfully');
        return true;
      } else {
        // Fallback to copy URL
        await navigator.clipboard.writeText(imageUrl);
        toast.success('Image URL copied to clipboard');
        return true;
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
      const errorData = await response.json();
      throw new Error(errorData.error || 'Failed to publish image');
    }

    const result = await response.json();
    toast.success(`Published to ${destination.name}`);
    return true;
  } catch (error) {
    console.error('Error publishing image:', error);
    toast.error(`Failed to publish to ${destination.name}`);
    return false;
  }
};
