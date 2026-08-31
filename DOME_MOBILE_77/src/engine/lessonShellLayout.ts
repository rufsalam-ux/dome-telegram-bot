export const DOME_LESSON_SHELL_SIZE={width:853,height:1280} as const;

export type ShellRect={left:number;top:number;width:number;height:number};

type SourceRect={x:number;y:number;width:number;height:number};

const SOURCE_RECTS={
  content:{x:132,y:202,width:608,height:602},
  replay:{x:143,y:1023,width:126,height:130},
  answer:{x:298,y:1000,width:174,height:174},
  hint:{x:493,y:1023,width:126,height:130},
  more:{x:635,y:1023,width:126,height:130},
  continue:{x:151,y:1174,width:561,height:75},
} satisfies Record<string,SourceRect>;

function mapRect(rect:SourceRect,scale:number,offsetX:number,offsetY:number):ShellRect{
  return {left:offsetX+rect.x*scale,top:offsetY+rect.y*scale,width:rect.width*scale,height:rect.height*scale};
}

export function lessonShellLayout(width:number,height:number){
  const safeWidth=Math.max(1,width);const safeHeight=Math.max(1,height);
  // The artwork contains the frame and physical controls as one composition.
  // Fitting it prevents tall phones from cover-cropping the cat and scenery.
  const scale=Math.min(safeWidth/DOME_LESSON_SHELL_SIZE.width,safeHeight/DOME_LESSON_SHELL_SIZE.height);
  const imageWidth=DOME_LESSON_SHELL_SIZE.width*scale;const imageHeight=DOME_LESSON_SHELL_SIZE.height*scale;
  const offsetX=(safeWidth-imageWidth)/2;const offsetY=(safeHeight-imageHeight)/2;
  const mappedContent=mapRect(SOURCE_RECTS.content,scale,offsetX,offsetY);const contentLeft=Math.max(4,mappedContent.left);const contentRight=Math.min(safeWidth-4,mappedContent.left+mappedContent.width);
  return {
    fitMode:'contain' as const,
    scale,
    image:{left:offsetX,top:offsetY,width:imageWidth,height:imageHeight},
    content:{...mappedContent,left:contentLeft,width:Math.max(1,contentRight-contentLeft)},
    controls:{
      replay:mapRect(SOURCE_RECTS.replay,scale,offsetX,offsetY),
      answer:mapRect(SOURCE_RECTS.answer,scale,offsetX,offsetY),
      hint:mapRect(SOURCE_RECTS.hint,scale,offsetX,offsetY),
      more:mapRect(SOURCE_RECTS.more,scale,offsetX,offsetY),
      continue:mapRect(SOURCE_RECTS.continue,scale,offsetX,offsetY),
    },
  };
}

export function shellContentIsClearOfControls(width:number,height:number):boolean{
  const layout=lessonShellLayout(width,height);const contentBottom=layout.content.top+layout.content.height;
  return Object.values(layout.controls).every(rect=>contentBottom<=rect.top);
}
