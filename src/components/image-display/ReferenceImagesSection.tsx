
import React from 'react';
import ReferenceImageIndicator from './ReferenceImageIndicator';

interface ReferenceImagesSectionProps {
  images: string[];
  onRemoveImage?: (index: number) => void;
}

// This component is being suppressed as per user requirements
const ReferenceImagesSection: React.FC<ReferenceImagesSectionProps> = ({ 
  images,
  onRemoveImage
}) => {
  // Always return null to prevent rendering
  return null;
};

export default ReferenceImagesSection;
