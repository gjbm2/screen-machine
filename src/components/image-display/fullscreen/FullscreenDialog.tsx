
import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDate } from "@/lib/utils";
import ReferenceImageDialog from "../ReferenceImageDialog";
import typedWorkflows from '@/data/typedWorkflows';
import refinersData from '@/data/refiners.json';

interface ImageInfoDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  image?: any;
  onDownload?: () => void;
}

const ImageInfoDialog: React.FC<ImageInfoDialogProps> = ({
  open,
  onOpenChange,
  image,
  onDownload
}) => {
  const [tab, setTab] = useState("info");
  const [showReferenceImage, setShowReferenceImage] = useState(false);
  const [referenceImages, setReferenceImages] = useState<string[]>([]);
  const [workflowName, setWorkflowName] = useState("");
  const [refinerName, setRefinerName] = useState("");
  
  useEffect(() => {
    if (image) {
      console.log('ImageInfoDialog opened with image:', image);
      
      // Process reference images
      let imageRefs: string[] = [];
      if (image.referenceImageUrl) {
        console.log('Reference image URL from image:', image.referenceImageUrl);
        if (typeof image.referenceImageUrl === 'string') {
          imageRefs.push(image.referenceImageUrl);
        } else if (Array.isArray(image.referenceImageUrl)) {
          imageRefs = image.referenceImageUrl;
        }
      }
      
      console.log('Processed reference images:', imageRefs);
      setReferenceImages(imageRefs);
      
      // Get workflow name
      const workflow = typedWorkflows.find(w => w.id === image.workflow);
      setWorkflowName(workflow?.name || image.workflow || "Unknown workflow");
      
      // Get refiner name
      if (image.refiner && image.refiner !== 'none') {
        const refiner = refinersData.find(r => r.id === image.refiner);
        setRefinerName(refiner?.name || image.refiner || "Unknown refiner");
      } else {
        setRefinerName("None");
      }
    }
  }, [image]);
  
  const handleOpenReferenceImage = () => {
    if (referenceImages.length > 0) {
      setShowReferenceImage(true);
    }
  };
  
  if (!image) return null;

  // Helper function to format parameter values for display
  const formatParamValue = (value: any) => {
    if (value === undefined || value === null) return 'Not set';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'object') return JSON.stringify(value);
    return value.toString();
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[600px] max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>Image Information</DialogTitle>
            <DialogDescription>
              Details about the generated image
            </DialogDescription>
          </DialogHeader>
          
          <Tabs value={tab} onValueChange={setTab} className="flex-1 flex flex-col">
            <TabsList className="grid grid-cols-2">
              <TabsTrigger value="info">Info</TabsTrigger>
              <TabsTrigger value="params">Parameters</TabsTrigger>
            </TabsList>
            
            <ScrollArea className="flex-1 mt-4">
              <TabsContent value="info" className="space-y-4">
                <div className="grid grid-cols-1 gap-4">
                  <div className="relative rounded-lg overflow-hidden aspect-[4/3] bg-muted">
                    <img 
                      src={image.url} 
                      alt="Generated image" 
                      className="object-contain w-full h-full"
                    />
                  </div>
                  
                  {referenceImages.length > 0 && (
                    <Button 
                      variant="outline" 
                      onClick={handleOpenReferenceImage}
                      className="w-full"
                    >
                      View Reference Image
                    </Button>
                  )}
                  
                  <div className="space-y-2">
                    <h3 className="font-medium">Prompt</h3>
                    <p className="text-sm text-muted-foreground">{image.prompt || "No prompt"}</p>
                  </div>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <h3 className="font-medium">Created</h3>
                      <p className="text-sm text-muted-foreground">
                        {image.timestamp ? formatDate(new Date(image.timestamp)) : "Unknown"}
                      </p>
                    </div>
                    <div>
                      <h3 className="font-medium">Workflow</h3>
                      <p className="text-sm text-muted-foreground">{workflowName}</p>
                    </div>
                  </div>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <h3 className="font-medium">Generation Time</h3>
                      <p className="text-sm text-muted-foreground">
                        {image.generation_time_seconds ? `${image.generation_time_seconds}s` : "Unknown"}
                      </p>
                    </div>
                    <div>
                      <h3 className="font-medium">Cost</h3>
                      <p className="text-sm text-muted-foreground">
                        {image.generation_cost_gbp ? `£${(image.generation_cost_gbp * 100).toFixed(1)}p` : "Unknown"}
                      </p>
                    </div>
                  </div>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <h3 className="font-medium">Refiner</h3>
                      <p className="text-sm text-muted-foreground">{refinerName}</p>
                    </div>
                    <div>
                      <h3 className="font-medium">Batch Index</h3>
                      <p className="text-sm text-muted-foreground">
                        {image.batchIndex !== undefined ? `#${image.batchIndex + 1}` : "Single image"}
                      </p>
                    </div>
                  </div>
                </div>
              </TabsContent>
              
              <TabsContent value="params" className="space-y-4">
                {/* Workflow Parameters */}
                <Card className="p-4">
                  <h3 className="font-medium mb-2">Workflow Parameters</h3>
                  <Separator className="mb-3" />
                  {image.params && Object.keys(image.params).length > 0 ? (
                    <div className="grid grid-cols-2 gap-y-2 gap-x-4">
                      {Object.entries(image.params).map(([key, value]) => (
                        <React.Fragment key={key}>
                          <div className="text-sm font-medium">{key}</div>
                          <div className="text-sm text-muted-foreground break-words">
                            {formatParamValue(value)}
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No workflow parameters</p>
                  )}
                </Card>
                
                {/* Global Parameters */}
                <Card className="p-4">
                  <h3 className="font-medium mb-2">Global Parameters</h3>
                  <Separator className="mb-3" />
                  {image.globalParams && Object.keys(image.globalParams).length > 0 ? (
                    <div className="grid grid-cols-2 gap-y-2 gap-x-4">
                      {Object.entries(image.globalParams).map(([key, value]) => (
                        <React.Fragment key={key}>
                          <div className="text-sm font-medium">{key}</div>
                          <div className="text-sm text-muted-foreground break-words">
                            {formatParamValue(value)}
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No global parameters</p>
                  )}
                </Card>
                
                {/* Refiner Parameters */}
                <Card className="p-4">
                  <h3 className="font-medium mb-2">Refiner Parameters</h3>
                  <Separator className="mb-3" />
                  {image.refinerParams && Object.keys(image.refinerParams).length > 0 ? (
                    <div className="grid grid-cols-2 gap-y-2 gap-x-4">
                      {Object.entries(image.refinerParams).map(([key, value]) => (
                        <React.Fragment key={key}>
                          <div className="text-sm font-medium">{key}</div>
                          <div className="text-sm text-muted-foreground break-words">
                            {formatParamValue(value)}
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No refiner parameters</p>
                  )}
                </Card>
              </TabsContent>
            </ScrollArea>
          </Tabs>
          
          <DialogFooter className="mt-4">
            {onDownload && (
              <Button onClick={onDownload}>
                Download Image
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      
      <ReferenceImageDialog
        isOpen={showReferenceImage}
        onOpenChange={setShowReferenceImage}
        imageUrls={referenceImages}
      />
    </>
  );
};

export default ImageInfoDialog;
