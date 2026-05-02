"use client"

import {
  ArrowDownIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  CheckIcon,
  CircleDotIcon,
  CircleIcon,
  Loader2Icon,
  PauseCircleIcon,
} from "lucide-react"
import { motion } from "motion/react"
import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

type Priority = "critical" | "high" | "medium" | "low"
type TaskStatus = "completed" | "in-progress" | "queued" | "paused"
type SortField = "priority" | "status" | "assignee" | "time"
type SortDir = "asc" | "desc"

interface AgentTask {
  id: string
  title: string
  description: string
  priority: Priority
  status: TaskStatus
  assignee: string
  estimatedTime: string
  endpoint?: string
}

const priorityWeight: Record<Priority, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
}

const statusWeight: Record<TaskStatus, number> = {
  "in-progress": 4,
  queued: 3,
  paused: 2,
  completed: 1,
}

const priorityConfig: Record<Priority, { dot: string; bg: string; text: string; label: string }> = {
  critical: {
    dot: "bg-red-500",
    bg: "bg-red-500/10",
    text: "text-red-600 dark:text-red-400",
    label: "Critical",
  },
  high: {
    dot: "bg-amber-500",
    bg: "bg-amber-500/10",
    text: "text-amber-600 dark:text-amber-400",
    label: "High",
  },
  medium: {
    dot: "bg-blue-500",
    bg: "bg-blue-500/10",
    text: "text-blue-600 dark:text-blue-400",
    label: "Medium",
  },
  low: {
    dot: "bg-muted-foreground/40",
    bg: "bg-muted/50",
    text: "text-muted-foreground",
    label: "Low",
  },
}

const statusConfig: Record<
  TaskStatus,
  { icon: typeof CheckIcon; iconClass: string; label: string }
> = {
  completed: {
    icon: CheckIcon,
    iconClass: "text-emerald-500",
    label: "Done",
  },
  "in-progress": {
    icon: Loader2Icon,
    iconClass: "text-blue-500 animate-spin",
    label: "Running",
  },
  queued: {
    icon: CircleIcon,
    iconClass: "text-muted-foreground/40",
    label: "Queued",
  },
  paused: {
    icon: PauseCircleIcon,
    iconClass: "text-amber-500",
    label: "Paused",
  },
}

const initialTasks: AgentTask[] = [
  {
    id: "t1",
    title: "Migrate authentication to JWT",
    description: "Replace express-session with stateless JWT tokens",
    priority: "critical",
    status: "in-progress",
    assignee: "Agent Alpha",
    estimatedTime: "~3 min",
    endpoint: "POST /api/auth/login",
  },
  {
    id: "t2",
    title: "Update API rate limiting middleware",
    description: "Implement sliding window rate limiter with Redis backend",
    priority: "high",
    status: "in-progress",
    assignee: "Agent Beta",
    estimatedTime: "~2 min",
    endpoint: "ALL /api/*",
  },
  {
    id: "t3",
    title: "Refactor database connection pooling",
    description: "Switch from single connection to PgBouncer pool with 20 max connections",
    priority: "high",
    status: "queued",
    assignee: "Agent Alpha",
    estimatedTime: "~5 min",
    endpoint: "src/lib/db.ts",
  },
  {
    id: "t4",
    title: "Add input validation to user endpoints",
    description: "Add Zod schemas for all user-facing request bodies",
    priority: "medium",
    status: "queued",
    assignee: "Agent Beta",
    estimatedTime: "~2 min",
    endpoint: "POST /api/users/*",
  },
  {
    id: "t5",
    title: "Write integration tests for auth flow",
    description: "Test login, refresh, logout, and concurrent session scenarios",
    priority: "medium",
    status: "paused",
    assignee: "Agent Alpha",
    estimatedTime: "~4 min",
    endpoint: "tests/auth.test.ts",
  },
  {
    id: "t6",
    title: "Update OpenAPI specification",
    description: "Sync OpenAPI 3.1 spec with current endpoint signatures",
    priority: "low",
    status: "completed",
    assignee: "Agent Beta",
    estimatedTime: "~1 min",
    endpoint: "docs/openapi.yaml",
  },
  {
    id: "t7",
    title: "Fix CORS configuration for staging",
    description: "Add staging.acme.com to allowed origins, remove wildcard",
    priority: "high",
    status: "completed",
    assignee: "Agent Alpha",
    estimatedTime: "~1 min",
    endpoint: "src/middleware/cors.ts",
  },
  {
    id: "t8",
    title: "Remove deprecated v1 API routes",
    description: "Delete /api/v1/* handlers and update client SDK references",
    priority: "low",
    status: "completed",
    assignee: "Agent Beta",
    estimatedTime: "~2 min",
    endpoint: "src/routes/v1/*",
  },
  {
    id: "t9",
    title: "Add structured logging with pino",
    description: "Replace console.log with pino JSON logger, add request ID tracing",
    priority: "medium",
    status: "queued",
    assignee: "Agent Alpha",
    estimatedTime: "~3 min",
    endpoint: "src/lib/logger.ts",
  },
  {
    id: "t10",
    title: "Implement webhook retry with exponential backoff",
    description: "Add retry queue for failed webhook deliveries with max 5 attempts",
    priority: "high",
    status: "queued",
    assignee: "Agent Beta",
    estimatedTime: "~4 min",
    endpoint: "src/services/webhooks.ts",
  },
]

export default function AiAgentTaskQueue() {
  const [tasks, setTasks] = useState(initialTasks)
  const [sortField, setSortField] = useState<SortField>("priority")
  const [sortDir, setSortDir] = useState<SortDir>("desc")

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(d => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortField(field)
      setSortDir("desc")
    }
  }

  const cycleStatus = (id: string) => {
    const order: TaskStatus[] = ["queued", "in-progress", "paused", "completed"]
    setTasks(prev =>
      prev.map(t => {
        if (t.id !== id) return t
        const currentIdx = order.indexOf(t.status)
        const nextStatus = order[(currentIdx + 1) % order.length]
        return { ...t, status: nextStatus }
      }),
    )
  }

  const sortedTasks = [...tasks].sort((a, b) => {
    const dir = sortDir === "asc" ? 1 : -1
    switch (sortField) {
      case "priority":
        return (priorityWeight[b.priority] - priorityWeight[a.priority]) * dir
      case "status":
        return (statusWeight[b.status] - statusWeight[a.status]) * dir
      case "assignee":
        return a.assignee.localeCompare(b.assignee) * dir
      case "time": {
        const aTime = Number.parseInt(a.estimatedTime.replace(/[^0-9]/g, ""), 10)
        const bTime = Number.parseInt(b.estimatedTime.replace(/[^0-9]/g, ""), 10)
        return (aTime - bTime) * dir
      }
      default:
        return 0
    }
  })

  const completed = tasks.filter(t => t.status === "completed").length
  const inProgress = tasks.filter(t => t.status === "in-progress").length
  const queued = tasks.filter(t => t.status === "queued").length
  const paused = tasks.filter(t => t.status === "paused").length
  const completionPercent = Math.round((completed / tasks.length) * 100)

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ArrowUpDownIcon className="size-3 text-muted-foreground/40" />
    return sortDir === "asc" ? (
      <ArrowUpIcon className="size-3" />
    ) : (
      <ArrowDownIcon className="size-3" />
    )
  }

  return (
    <section className="mx-auto w-full max-w-4xl p-4">
      <div className="overflow-hidden rounded-lg border bg-card">
        {/* Header */}
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <CircleDotIcon className="size-4 text-muted-foreground" />
          <div className="flex-1">
            <span className="font-medium text-sm">Task Queue</span>
            <p className="text-muted-foreground text-xs">{tasks.length} tasks across 2 agents</p>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span className="size-1.5 rounded-full bg-blue-500" />
              {inProgress} running
            </span>
            <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span className="size-1.5 rounded-full bg-muted-foreground/30" />
              {queued} queued
            </span>
            {paused > 0 && (
              <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span className="size-1.5 rounded-full bg-amber-500" />
                {paused} paused
              </span>
            )}
          </div>
          <Badge variant="secondary" className="font-mono font-normal text-xs">
            {completionPercent}%
          </Badge>
        </div>

        {/* Progress bar */}
        <div className="border-b px-4 py-2">
          <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
            <motion.div
              className="h-full rounded-full bg-foreground"
              initial={{ width: 0 }}
              animate={{ width: `${completionPercent}%` }}
              transition={{ duration: 0.5, ease: "easeOut" }}
            />
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-10 pl-4 pr-0">
                  <span className="sr-only">Status</span>
                </TableHead>
                <TableHead className="min-w-[200px]">Task</TableHead>
                <TableHead className="w-24">
                  <button
                    type="button"
                    onClick={() => toggleSort("priority")}
                    className="flex items-center gap-1 text-xs"
                  >
                    Priority
                    <SortIcon field="priority" />
                  </button>
                </TableHead>
                <TableHead className="w-24">
                  <button
                    type="button"
                    onClick={() => toggleSort("status")}
                    className="flex items-center gap-1 text-xs"
                  >
                    Status
                    <SortIcon field="status" />
                  </button>
                </TableHead>
                <TableHead className="w-28">
                  <button
                    type="button"
                    onClick={() => toggleSort("assignee")}
                    className="flex items-center gap-1 text-xs"
                  >
                    Agent
                    <SortIcon field="assignee" />
                  </button>
                </TableHead>
                <TableHead className="w-20 pr-4">
                  <button
                    type="button"
                    onClick={() => toggleSort("time")}
                    className="flex items-center gap-1 text-xs"
                  >
                    Est.
                    <SortIcon field="time" />
                  </button>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedTasks.map((task, index) => {
                const sConfig = statusConfig[task.status]
                const StatusIcon = sConfig.icon
                const pConfig = priorityConfig[task.priority]
                return (
                  <motion.tr
                    key={task.id}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{
                      duration: 0.15,
                      delay: index * 0.03,
                    }}
                    className="group border-b transition-colors last:border-b-0 hover:bg-muted/50"
                  >
                    <TableCell className="pl-4 pr-0">
                      <button
                        type="button"
                        onClick={() => cycleStatus(task.id)}
                        className="flex size-5 items-center justify-center rounded transition-colors hover:bg-muted"
                        title={`Status: ${sConfig.label}. Click to cycle.`}
                      >
                        <StatusIcon className={cn("size-3.5", sConfig.iconClass)} />
                      </button>
                    </TableCell>
                    <TableCell>
                      <div className="min-w-0">
                        <span
                          className={cn(
                            "text-sm",
                            task.status === "completed" && "text-muted-foreground line-through",
                          )}
                        >
                          {task.title}
                        </span>
                        {task.endpoint && (
                          <p className="mt-0.5 truncate font-mono text-muted-foreground text-[10px]">
                            {task.endpoint}
                          </p>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="secondary"
                        className={cn("font-normal text-[10px]", pConfig.bg, pConfig.text)}
                      >
                        <span className={cn("mr-1 size-1.5 rounded-full", pConfig.dot)} />
                        {pConfig.label}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <span
                          className={cn(
                            "size-1.5 rounded-full",
                            task.status === "completed" && "bg-emerald-500",
                            task.status === "in-progress" && "bg-blue-500",
                            task.status === "queued" && "bg-muted-foreground/30",
                            task.status === "paused" && "bg-amber-500",
                          )}
                        />
                        {sConfig.label}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="font-mono text-muted-foreground text-xs">
                        {task.assignee.split(" ").pop()}
                      </span>
                    </TableCell>
                    <TableCell className="pr-4">
                      <span className="font-mono text-muted-foreground text-xs">
                        {task.estimatedTime}
                      </span>
                    </TableCell>
                  </motion.tr>
                )
              })}
            </TableBody>
          </Table>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t px-4 py-2.5">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-muted-foreground text-xs">
              <span className="size-1.5 rounded-full bg-emerald-500" />
              {completed} done
            </span>
            <span className="text-muted-foreground text-xs">{tasks.length} total</span>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={() => setTasks(initialTasks)}
            >
              Reset
            </Button>
            <Button variant="outline" size="sm" className="h-7 text-xs">
              Run All Queued
            </Button>
          </div>
        </div>
      </div>
    </section>
  )
}
