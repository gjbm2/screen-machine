import React, { useState, useEffect } from 'react';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';
import { Api, PublishDestination } from '@/utils/api';
import { BucketImage } from '@/components/image-display/view-modes/BucketView/BucketImage';
import { toast } from 'sonner';

interface BucketGridViewProps {
  bucketId: string;
  onImageClick?: (image: any) => void;
}

const api = new Api();

export function BucketGridView({ bucketId, onImageClick }: BucketGridViewProps) {
  const [images, setImages] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { destinations, loading: destinationsLoading } = usePublishDestinations();

  useEffect(() => {
    async function fetchData() {
      try {
        const [bucketDetails, destinations] = await Promise.all([
          api.getBucketDetails(bucketId),
          api.getPublishDestinations()
        ]);
        setImages(bucketDetails.items);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch bucket details');
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, [bucketId]);

  const handleToggleFavorite = async (filename: string, currentState: boolean) => {
    try {
      const newState = await api.toggleFavorite(bucketId, filename, currentState);
      setImages(prevImages => 
        prevImages.map(img => 
          img.filename === filename ? { ...img, favorite: newState } : img
        )
      );
    } catch (err) {
      console.error('Failed to toggle favorite:', err);
    }
  };

  const handleDelete = async (filename: string) => {
    try {
      await api.deleteImage(bucketId, filename);
      setImages(prevImages => prevImages.filter(img => img.filename !== filename));
    } catch (err) {
      console.error('Failed to delete image:', err);
    }
  };

  const handleCopy = async (filename: string, targetBucketId: string) => {
    try {
      await api.copyImageToBucket(bucketId, targetBucketId, filename, true);
    } catch (err) {
      console.error('Failed to copy image:', err);
    }
  };

  const handleMoveUp = async (filename: string) => {
    try {
      await api.moveImage(bucketId, filename, 'up');
      setImages(prevImages => {
        const index = prevImages.findIndex(img => img.filename === filename);
        if (index <= 0) return prevImages;
        const newImages = [...prevImages];
        [newImages[index - 1], newImages[index]] = [newImages[index], newImages[index - 1]];
        return newImages;
      });
    } catch (err) {
      console.error('Failed to move image up:', err);
    }
  };

  const handleMoveDown = async (filename: string) => {
    try {
      await api.moveImage(bucketId, filename, 'down');
      setImages(prevImages => {
        const index = prevImages.findIndex(img => img.filename === filename);
        if (index >= prevImages.length - 1) return prevImages;
        const newImages = [...prevImages];
        [newImages[index], newImages[index + 1]] = [newImages[index + 1], newImages[index]];
        return newImages;
      });
    } catch (err) {
      console.error('Failed to move image down:', err);
    }
  };

  const handleOpen = (image: any) => {
    if (onImageClick) {
      onImageClick(image);
    }
  };

  const handlePublish = async (filename: string, destinationId: string) => {
    try {
      const result = await api.publishFromBucket(bucketId, filename, false);
      if (result.success) {
        toast.success('Image published successfully');
      } else {
        toast.error(result.error || 'Failed to publish image');
      }
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.error('Failed to publish image');
    }
  };

  if (loading || destinationsLoading) {
    return <div>Loading...</div>;
  }

  if (error) {
    return <div>Error: {error}</div>;
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 p-4">
      {images.map((image) => (
        <BucketImage
          key={image.filename}
          bucket={bucketId}
          item={image}
          buckets={destinations}
          onToggleFavorite={(currentState) => handleToggleFavorite(image.filename, currentState)}
          onDelete={() => handleDelete(image.filename)}
          onCopy={(targetBucketId) => handleCopy(image.filename, targetBucketId)}
          onMoveUp={() => handleMoveUp(image.filename)}
          onMoveDown={() => handleMoveDown(image.filename)}
          onOpen={() => handleOpen(image)}
          onPublish={(destinationId) => handlePublish(image.filename, destinationId)}
        />
      ))}
    </div>
  );
}
