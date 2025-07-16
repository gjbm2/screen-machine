interface Window {
  externalImageUrls: string[];
  imageCounter?: number;
  addImageReferenceToPrompt: (sourceUrl: string, bucketId: string, imageId: string) => void;
  generateImage: (params: {
    prompt: string;
    batch_id: string;
    workflow?: string;
    [key: string]: any;
  }) => Promise<void>;
} 