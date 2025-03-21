
import React from 'react';

interface ReferenceImageSectionProps {
  referenceImageUrl: string;
  onReferenceImageClick: () => void;
}

const ReferenceImageSection: React.FC<ReferenceImageSectionProps> = ({
  referenceImageUrl,
  onReferenceImageClick
}) => {
  return (
    <div className="mt-4 border-t pt-4">
      <p className="text-sm text-muted-foreground mb-2">Reference image:</p>
      <div className="flex justify-center">
        <div className="border rounded-md overflow-hidden w-24 h-24">
          <img 
            src={referenceImageUrl} 
            alt="Reference image"
            className="w-full h-full object-cover cursor-pointer"
            onClick={onReferenceImageClick}
          />
        </div>
      </div>
    </div>
  );
};

export default ReferenceImageSection;
