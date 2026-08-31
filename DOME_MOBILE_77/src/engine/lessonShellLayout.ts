export const DOME_LESSON_SHELL_SIZE={width:841,height:1870} as const;

export type ShellRect={left:number;top:number;width:number;height:number};

type SourceRect={x:number;y:number;width:number;height:number};

const SOURCE_RECTS={
  content:{x:126,y:506,width:598,height:706},
  replay:{x:137,y:1417,width:128,height:135},
  answer:{x:287,y:1390,width:181,height:181},
  hint:{x:472,y:1417,width:130,height:135},
  more:{x:617,y:1417,width:131,height:135},
  continue:{x:148,y:1575,width:545,height:80},
} satisfies Record<string,SourceRect>;

function mapRect(rect:SourceRect,scale:number,offsetX:number,offsetY:number):ShellRect{
  return {left:offsetX+rect.x*scale,top:offsetY+rect.y*scale,width:rect.width*scale,height:rect.height*scale};
}

export function lessonShellLayout(width:number,height:number){
  const safeWidth=Math.max(1,width);const safeHeight=Math.max(1,height);
  // This release asset was outpainted to the common phone aspect ratio. A
  // minimal edge fill avoids synthetic blue bands; only the added outer
  // scenery may leave the viewport while the authored panel and controls stay.
  const scale=Math.max(safeWidth/DOME_LESSON_SHELL_SIZE.width,safeHeight/DOME_LESSON_SHELL_SIZE.height);
  const imageWidth=DOME_LESSON_SHELL_SIZE.width*scale;const imageHeight=DOME_LESSON_SHELL_SIZE.height*scale;
  const offsetX=(safeWidth-imageWidth)/2;const offsetY=(safeHeight-imageHeight)/2;
  const mappedContent=mapRect(SOURCE_RECTS.content,scale,offsetX,offsetY);const contentLeft=Math.max(4,mappedContent.left);const contentRight=Math.min(safeWidth-4,mappedContent.left+mappedContent.width);
  return {
    fitMode:'extended-fill' as const,
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
