
import React, { useState } from 'react';
import { BucketGridView } from './BucketView/BucketGridView';
import { BucketItem } from '@/api/buckets-api';

interface SmallGridViewProps {
  images: any[];
  isLoading: boolean;
  onSmallImageClick: (image: any) => void;
  onCreateAgain: (batchId?: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
}

const SmallGridView: React.FC<SmallGridViewProps> = ({
  images,
  isLoading,
  onSmallImageClick,
  onCreateAgain,
  onDeleteImage
}) => {
  // Handle opening a bucket image in fullscreen view
  const handleOpenBucketImage = (bucketImage: BucketItem) => {
    console.log('Opening bucket image in fullscreen:', bucketImage);
    
    // Create a compatible image object that the existing fullscreen view can understand
    const compatibleImage = {
      url: bucketImage.thumbnail.replace('/thumbnail/', '/raw/'),
      prompt: bucketImage.filename, // Use filename as prompt since we don't have actual prompt data
      batchId: `bucket-${bucketImage.bucket}-${bucketImage.filename}`,
      batchIndex: bucketImage.index,
      status: 'completed',
      timestamp: Date.now()
    };

    onSmallImageClick(compatibleImage);
  };

  return (
    <div className="bucket-grid-container w-full">
      <BucketGridView onFullScreenView={handleOpenBucketImage} />
    </div>
  );
};

export default SmallGridView;
