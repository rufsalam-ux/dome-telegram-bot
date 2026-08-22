from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class PacingDecision:
    mode:str
    allow_extra_discussion:bool
    suggested_followups:int
    reading_share_delta:float

def decide_pacing(*,elapsed_minutes:float,step:int,total_steps:int,target_minutes:float=35.0)->PacingDecision:
    if total_steps<=0: return PacingDecision('normal',True,1,0.0)
    progress=max(0.0,min(1.0,step/total_steps)); expected=target_minutes*progress
    drift=elapsed_minutes-expected
    if drift>5: return PacingDecision('catch_up',False,0,-0.15)  # AI takes a little more reading / fewer extras
    if drift<-5: return PacingDecision('enrich',True,2,0.10)    # learner can take more / discuss more
    return PacingDecision('normal',True,1,0.0)
