/*
import { toast } from 'sonner';

interface PublishImageOptions {
  imageUrl: string;
  destination: string;
  destinationType: string;
  destinationFile?: string;
  metadata?: {
    prompt?: string;
    workflow?: string;
    params?: Record<string, any>;
  };
}

export const publishImageToBackend = async (options: PublishImageOptions): Promise<boolean> => {
  try {
    // Make API call to the backend
    const response = await fetch('/api/publish-image', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(options),
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error || 'Failed to publish image');
    }

    const result = await response.json();
    
    if (result.success) {
      toast.success(`Published to ${options.destination}`);
      return true;
    } else {
      toast.error(result.error || 'Failed to publish image');
      return false;
    }
  } catch (error) {
    console.error('Error publishing image to backend:', error);
    toast.error(`Failed to publish to ${options.destination}`);
    return false;
  }
};
*/