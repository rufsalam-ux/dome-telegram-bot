export type HomeMenuLayout={
  compact:boolean;
  columns:number;
  contentPadding:number;
  gap:number;
  heroSize:number;
  tileHeight:number;
  availableHeight:number;
  estimatedHeight:number;
};

export function homeMenuLayoutPolicy(width:number,height:number,bottomInset=0):HomeMenuLayout{
  const safeWidth=Math.max(280,Number(width)||360);
  const safeHeight=Math.max(320,Number(height)||640);
  const availableHeight=Math.max(300,safeHeight-Math.max(0,Number(bottomInset)||0));
  const landscape=safeWidth>safeHeight;
  const compact=availableHeight<760;
  const columns=landscape?4:safeWidth>=700?3:2;
  const contentPadding=availableHeight<680?10:16;
  const gap=availableHeight<680?5:7;
  const heroSize=landscape?48:availableHeight<680?58:82;
  const tileHeight=availableHeight<680?42:46;
  const rows=Math.ceil(8/columns);
  const headerHeight=heroSize;
  const summaryHeight=compact?48:58;
  const primaryHeight=44;
  const estimatedHeight=contentPadding*2+headerHeight+summaryHeight+primaryHeight+rows*tileHeight+Math.max(0,rows-1)*gap+gap*4;
  return {compact,columns,contentPadding,gap,heroSize,tileHeight,availableHeight,estimatedHeight};
}

export function homeMenuFitsWithoutScroll(width:number,height:number,bottomInset=0):boolean{
  const layout=homeMenuLayoutPolicy(width,height,bottomInset);
  return layout.estimatedHeight<=layout.availableHeight;
}
