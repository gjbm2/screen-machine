
import { useDebugPanelCaption } from './useDebugPanelCaption';
import { useDebugPanelMetadata } from './useDebugPanelMetadata';
import { useDebugPanelMetadataTag } from './useDebugPanelMetadataTag';
import { useDebugPanelFileSelector } from './useDebugPanelFileSelector';
import { useDebugPanelResize } from './useDebugPanelResize';
import { useDebugPanelPreview } from './useDebugPanelPreview';

interface UseDebugPanelConfigurationProps {
  caption: string | null;
  setCaption: (caption: string | null) => void;
  selectFile: (file: string) => () => void;
  isCurrentFile: (file: string, imageUrl: string | null) => boolean;
  imageUrl: string | null;
  metadata: Record<string, string>;
  setMetadataEntries: (entries: Array<{key: string, value: string}>) => void;
  previewCaption: string | null;
  setPreviewCaption: (caption: string | null) => void;
  onApplyCaption: (caption: string | null) => void;
  handleResizeStart: (e: React.MouseEvent) => void;
}

export const useDebugPanelConfiguration = ({
  caption,
  setCaption,
  selectFile,
  isCurrentFile,
  imageUrl,
  metadata,
  setMetadataEntries,
  previewCaption,
  setPreviewCaption,
  onApplyCaption,
  handleResizeStart: positionResizeHandler
}: UseDebugPanelConfigurationProps) => {
  // Hooks for specific functionality
  const { 
    insertMetadataTag: getMetadataTagHandler,
    insertAllMetadata: getAllMetadataHandler
  } = useDebugPanelCaption({
    caption,
    metadataEntries: Object.entries(metadata).map(([key, value]) => ({ key, value: String(value) })),
    setPreviewCaption,
    onApplyCaption,
    imageUrl
  });

  const {
    handleRefreshMetadata
  } = useDebugPanelMetadata({
    imageUrl,
    metadata,
    setMetadataEntries
  });

  const {
    insertMetadataTag,
    insertAllMetadata
  } = useDebugPanelMetadataTag({
    caption,
    getMetadataTagHandler,
    getAllMetadataHandler,
    setCaption
  });

  const {
    selectFileHandler,
    isCurrentFileHandler
  } = useDebugPanelFileSelector({
    selectFile,
    isCurrentFile,
    imageUrl
  });

  const {
    handleResizeStartInternal
  } = useDebugPanelResize({
    positionResizeHandler
  });

  // Preview handling hook
  useDebugPanelPreview({
    previewCaption,
    onApplyCaption,
    imageUrl
  });

  return {
    insertMetadataTag,
    insertAllMetadata,
    selectFileHandler,
    isCurrentFileHandler,
    handleRefreshMetadata,
    handleResizeStartInternal
  };
};
