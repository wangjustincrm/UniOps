/**
 * ★ UAT 15: "on all forms that have a risk assessment completed, place the
 * information under the button and display it like the risk assessment on the
 * incident form." One section for every form that is scored against the risk
 * matrix: its heading with the current rating, the green button, and under it
 * every assessment made — the incident's layout, everywhere.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { SectionTitle } from '@/components/ui'
import { RatingBadge, RiskScoringHistory, useScorings } from '@/components/RiskScoring'
import { RiskAssessmentDialog, type HazardBeingScored } from '@/components/RiskAssessmentDialog'
import { assessButton } from '@/components/FoodSafetyRisk'

export default function RiskAssessmentSection({
  sourceType, sourceId, mayAssess, hazard, showHazard = false, emptyNote, onAssessed, className = '',
}: {
  sourceType: 'incident' | 'hazard'
  sourceId: string
  mayAssess: boolean
  /** A register hazard is itself the hazard scored (see RiskAssessmentDialog). */
  hazard?: HazardBeingScored
  showHazard?: boolean
  emptyNote: string
  onAssessed?: () => void
  className?: string
}) {
  const [assessing, setAssessing] = useState(false)
  const queryClient = useQueryClient()
  const latest = useScorings(sourceType, sourceId).data?.[0]?.rating ?? null
  return (
    <section className={className} aria-label="Risk assessment">
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <SectionTitle className="mb-0">Risk assessment</SectionTitle>
        {latest && <RatingBadge rating={latest} />}
      </div>
      {mayAssess && (
        <button type="button" onClick={() => setAssessing(true)} className={assessButton}>
          {latest ? 'Assess the risk again' : 'Assess the risk'}
        </button>
      )}
      <RiskScoringHistory sourceType={sourceType} sourceId={sourceId} showHazard={showHazard}
                          emptyNote={emptyNote} />
      {assessing && (
        <RiskAssessmentDialog sourceType={sourceType} sourceId={sourceId} hazard={hazard}
                              onClose={() => {
                                setAssessing(false)
                                if (sourceType === 'hazard') {
                                  void queryClient.invalidateQueries({ queryKey: ['hazards'] })
                                  void queryClient.invalidateQueries({ queryKey: ['hazard', sourceId] })
                                }
                                onAssessed?.()
                              }} />
      )}
    </section>
  )
}
