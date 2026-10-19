## Frontier 0.27.46

- Adaptive now labels classifier certainty explicitly as task-classification confidence rather than implying confidence in the selected agent.
- Classifications below 60% raise the turn's reasoning, planning, review, repository, and instruction-following requirements so uncertain requests route more conservatively.
