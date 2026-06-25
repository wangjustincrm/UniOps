import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  projectService,
  type ProjectFilters,
  type CreateProjectBody,
  type UpdateProjectBody,
} from '@/services/projects'

export function useProjects(filters?: ProjectFilters) {
  return useQuery({
    queryKey: ['projects', filters],
    queryFn: () => projectService.list(filters),
  })
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ['projects', id],
    queryFn: () => projectService.get(id),
    enabled: Boolean(id),
  })
}

export function useCreateProject() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CreateProjectBody) => projectService.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export function useUpdateProject() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateProjectBody }) =>
      projectService.update(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['projects', id] })
    },
  })
}
