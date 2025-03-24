
import { MetadataEntry } from '../types';

interface UseDebugPanelMetadataTagProps {
  caption: string | null;
  getMetadataTagHandler: (key: string) => () => string | null;
  getAllMetadataHandler: () => string;
  setCaption: (caption: string | null) => void;
}

export const useDebugPanelMetadataTag = ({
  caption,
  getMetadataTagHandler,
  getAllMetadataHandler,
  setCaption
}: UseDebugPanelMetadataTagProps) => {
  
  const insertMetadataTag = (key: string) => {
    const handler = getMetadataTagHandler(key);
    const newCaption = handler();
    if (newCaption) {
      setCaption(newCaption);
    }
  };

  const insertAllMetadata = () => {
    const newCaption = getAllMetadataHandler();
    setCaption(newCaption);
  };

  return {
    insertMetadataTag,
    insertAllMetadata
  };
};
