
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { CaptionTab } from '../CaptionTab';
import { CaptionPosition } from '../../types';

interface CaptionPanelProps {
  caption: string;
  previewCaption: string | null;
  captionPosition: CaptionPosition;
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  setCaption: (value: string) => void;
  setCaptionPosition: (value: CaptionPosition) => void;
  setCaptionSize: (value: string) => void;
  setCaptionColor: (value: string) => void;
  setCaptionFont: (value: string) => void;
  setCaptionBgColor: (value: string) => void;
  setCaptionBgOpacity: (value: number) => void;
  insertAllMetadata: () => void;
}

export const CaptionPanel: React.FC<CaptionPanelProps> = ({
  caption,
  previewCaption,
  captionPosition,
  captionSize,
  captionColor,
  captionFont,
  captionBgColor,
  captionBgOpacity,
  setCaption,
  setCaptionPosition,
  setCaptionSize,
  setCaptionColor,
  setCaptionFont,
  setCaptionBgColor,
  setCaptionBgOpacity,
  insertAllMetadata
}) => {
  return (
    <CardContent className="p-0 flex-1 overflow-auto">
      <CaptionTab 
        caption={caption}
        previewCaption={previewCaption}
        captionPosition={captionPosition}
        captionSize={captionSize}
        captionColor={captionColor}
        captionFont={captionFont}
        captionBgColor={captionBgColor}
        captionBgOpacity={captionBgOpacity}
        setCaption={setCaption}
        setCaptionPosition={setCaptionPosition}
        setCaptionSize={setCaptionSize}
        setCaptionColor={setCaptionColor}
        setCaptionFont={setCaptionFont}
        setCaptionBgColor={setCaptionBgColor}
        setCaptionBgOpacity={setCaptionBgOpacity}
        insertAllMetadata={insertAllMetadata}
      />
    </CardContent>
  );
};
