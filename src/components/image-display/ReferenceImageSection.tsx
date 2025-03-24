
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
    <div className="mt-2 border-t pt-2">
      <p className="text-sm text-muted-foreground mb-1">Reference image:</p>
      <div className="flex justify-center">
        <div className="border rounded-md overflow-hidden w-16 h-16">
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
