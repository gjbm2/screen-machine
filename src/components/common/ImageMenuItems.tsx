// One picture's ⋯ menu, the same wherever the picture is shown small or large (a bucket tile, Generated's thumbnails
// and its selected picture, the Loope's More): Info, Bring to life, Use as prompt, Start again, Publish to, Copy to,
// Move to, Open in a new tab, Delete. Each item shows when its handler is given (owner, 19 Nov: the options had
// drifted apart between the thumbnails and the large picture). `except` leaves out what a surface already has as a
// button of its own (the Loope's bar).
import React from 'react';
import { Copy, ExternalLink, Info, Move, RefreshCw, Share, Sparkles, Trash, Wand2 } from 'lucide-react';
import { toast } from 'sonner';
import { DropdownMenuItem, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger } from '@/components/ui/dropdown-menu';
import { animateStill } from '@/shared/api/buckets';
import { isVideoFile } from '@/utils/image-utils';
import type { ImageItem } from '@/types/image-types';

type Destination = { id: string; name: string; headless: boolean };

export interface ImageMenuHandlers {
  onInfo?: (img: ImageItem) => void;
  onUseAsPrompt?: (img: ImageItem) => void;
  /** Start again: the prompt and its reference pictures back in the generator (shown when it has references). */
  onStartAgain?: (img: ImageItem) => void;
  onCopyTo?: (img: ImageItem, destId: string) => void;
  onMoveTo?: (img: ImageItem, destId: string) => void;
  onPublish?: (img: ImageItem, destId: string) => void;
  publishDestinations?: Destination[];
  onDelete?: (img: ImageItem) => void;
}

interface Props extends ImageMenuHandlers {
  image: ImageItem;
  /** The bucket the picture is in (Bring to life, Raw URL). */
  bucket?: string | null;
  except?: Array<'info' | 'useAsPrompt' | 'fullScreen' | 'rawUrl'>;
}

function item(e: React.MouseEvent | Event) {
  e.stopPropagation();
}

function Submenu({ icon, label, destinations, onPick }: {
  icon: React.ReactNode; label: string; destinations: Destination[]; onPick: (destId: string) => void;
}) {
  if (!destinations.length) return null;
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger onClick={item}>
        {icon} {label}
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent>
        {destinations.map((d) => (
          <DropdownMenuItem key={d.id} onClick={(e) => { item(e); onPick(d.id); }}>
            {d.name || d.id}
          </DropdownMenuItem>
        ))}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}

export function ImageMenuItems({
  image, bucket, onInfo, onUseAsPrompt, onStartAgain, onCopyTo, onMoveTo, onPublish, publishDestinations, onDelete,
  except = [],
}: Props) {
  const isVideo = image.mediaType === 'video' || isVideoFile(image.urlFull || image.id);
  const where = bucket || image.bucketId;
  const destinations = publishDestinations || [];
  return (
    <>
      {onInfo && !except.includes('info') && (
        <DropdownMenuItem onClick={(e) => { item(e); onInfo(image); }}>
          <Info className="h-4 w-4 mr-2" /> Info
        </DropdownMenuItem>
      )}
      {where && !isVideo && (
        <DropdownMenuItem
          onClick={(e) => {
            item(e);
            animateStill(where, image.id).then(
              () => toast.success('Making a clip: it appears with the picture in a few minutes'),
              (err) => toast.error(String(err?.message || err)),
            );
          }}
        >
          <Wand2 className="h-4 w-4 mr-2" /> Bring to life
        </DropdownMenuItem>
      )}
      {onUseAsPrompt && !except.includes('useAsPrompt') && (
        <DropdownMenuItem onClick={(e) => { item(e); onUseAsPrompt(image); }}>
          <Sparkles className="h-4 w-4 mr-2" /> Use as prompt
        </DropdownMenuItem>
      )}
      {onStartAgain && (
        <DropdownMenuItem onClick={(e) => { item(e); onStartAgain(image); }}>
          <RefreshCw className="h-4 w-4 mr-2" /> Start again
        </DropdownMenuItem>
      )}
      {onPublish && (
        <Submenu icon={<Share className="h-4 w-4 mr-2" />} label="Publish to…" destinations={destinations.filter((d) => !d.headless)}
          onPick={(id) => onPublish(image, id)} />
      )}
      {onCopyTo && (
        <Submenu icon={<Copy className="h-4 w-4 mr-2" />} label="Copy to…" destinations={destinations}
          onPick={(id) => onCopyTo(image, id)} />
      )}
      {onMoveTo && (
        <Submenu icon={<Move className="h-4 w-4 mr-2" />} label="Move to…" destinations={destinations}
          onPick={(id) => onMoveTo(image, id)} />
      )}
      {/* one way to the file itself, and it says it opens a tab (owner, 20 Nov: a new tab without warning; "Full
          screen" and "Raw URL" both did that). Looking closely is the viewer's job */}
      {(where || image.urlFull) && !except.includes('rawUrl') && (
        <DropdownMenuItem
          onClick={(e) => { item(e); window.open(where ? `/output/${where}/${image.id}` : image.urlFull, '_blank'); }}
        >
          <ExternalLink className="h-4 w-4 mr-2" /> Open in a new tab
        </DropdownMenuItem>
      )}
      {onDelete && (
        <DropdownMenuItem
          onClick={(e) => {
            item(e);
            if (image.isFavourite && !confirm('Are you sure you want to delete this favorite image?')) return;
            onDelete(image);
          }}
        >
          <Trash className="h-4 w-4 mr-2" /> Delete
        </DropdownMenuItem>
      )}
    </>
  );
}
